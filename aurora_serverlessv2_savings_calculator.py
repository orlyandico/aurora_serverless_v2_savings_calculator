#!/usr/bin/env python3

"""
Aurora Serverless Cost Calculator

Calculates potential savings from moving existing RDS fleet to Aurora Serverless V2.

Notes:
- Assumes 1 ACU = 0.25 vCPU
- Assumes all instances are On-Demand (doesn't factor in Reserved Instances)
- Limited support for burstable instances
- Only queries RDS fleet in current AWS CLI region
- Requires properly configured AWS CLI with appropriate permissions

Author: Adapted from original by orly.andico@gmail.com
"""

import boto3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import os
from typing import Tuple, Dict, Any

def get_aurora_serverless_pricing(region_name: str, database_engine: str) -> Tuple[float, float]:
    """
    Get ACU and IOPS pricing for Aurora MySQL/PostgreSQL in the specified region.
    
    Args:
        region_name: AWS region name
        database_engine: Either "Aurora MySQL" or "Aurora PostgreSQL"
        
    Returns:
        Tuple of (ACU price, IOPS price)
    """
    pricing_client = boto3.client('pricing', region_name='us-east-1')

    # Get IO operation cost (DB engine independent)
    io_filters = [
        {'Type': 'TERM_MATCH', 'Field': 'regionCode', 'Value': region_name},
        {'Type': 'TERM_MATCH', 'Field': 'productFamily', 'Value': 'System Operation'},
        {'Type': 'TERM_MATCH', 'Field': 'databaseEngine', 'Value': 'Any'},
        {'Type': 'TERM_MATCH', 'Field': 'group', 'Value': 'Aurora I/O Operation'}
    ]
    io_response = pricing_client.get_products(ServiceCode='AmazonRDS', Filters=io_filters, MaxResults=1)
    
    io_pricing = json.loads(io_response['PriceList'][0])
    io_terms = io_pricing['terms']['OnDemand']
    io_id1 = list(io_terms)[0]
    io_id2 = list(io_terms[io_id1]['priceDimensions'])[0]
    price_iops = float(io_terms[io_id1]['priceDimensions'][io_id2]['pricePerUnit']['USD'])

    # Get ACU cost
    acu_filters = [
        {'Type': 'TERM_MATCH', 'Field': 'regionCode', 'Value': region_name},
        {'Type': 'TERM_MATCH', 'Field': 'databaseEngine', 'Value': database_engine},
        {'Type': 'TERM_MATCH', 'Field': 'productFamily', 'Value': 'ServerlessV2'}
    ]
    acu_response = pricing_client.get_products(ServiceCode='AmazonRDS', Filters=acu_filters, MaxResults=1)
    
    acu_pricing = json.loads(acu_response['PriceList'][0])
    acu_terms = acu_pricing['terms']['OnDemand']
    acu_id1 = list(acu_terms)[0]
    acu_id2 = list(acu_terms[acu_id1]['priceDimensions'])[0]
    price_acu = float(acu_terms[acu_id1]['priceDimensions'][acu_id2]['pricePerUnit']['USD'])

    return price_acu, price_iops

def get_rds_instance_hourly_price(region_name: str, instance_type: str, 
                                database_engine: str, deployment_option: str) -> Dict[str, Any]:
    """
    Get hourly pricing data for an RDS instance type.
    
    Args:
        region_name: AWS region name
        instance_type: RDS instance type (e.g., 'db.r5.large')
        database_engine: Database engine type
        deployment_option: Either 'Single-AZ' or 'Multi-AZ'
        
    Returns:
        Dictionary containing instance details and pricing
    """
    filters = [
        {'Type': 'TERM_MATCH', 'Field': 'instanceType', 'Value': instance_type},
        {'Type': 'TERM_MATCH', 'Field': 'databaseEngine', 'Value': database_engine},
        {'Type': 'TERM_MATCH', 'Field': 'licenseModel', 'Value': 'No License required'},
        {'Type': 'TERM_MATCH', 'Field': 'deploymentOption', 'Value': deployment_option},
        {'Type': 'TERM_MATCH', 'Field': 'regionCode', 'Value': region_name}
    ]

    pricing_client = boto3.client('pricing', region_name='us-east-1')
    response = pricing_client.get_products(ServiceCode='AmazonRDS', Filters=filters, MaxResults=1)

    pricing_data = json.loads(response['PriceList'][0])
    on_demand = pricing_data['terms']['OnDemand']
    id1 = list(on_demand)[0]
    id2 = list(on_demand[id1]['priceDimensions'])[0]
    price = on_demand[id1]['priceDimensions'][id2]['pricePerUnit']['USD']

    return {
        'vcpu': float(pricing_data['product']['attributes']['vcpu']),
        'memory': float(pricing_data['product']['attributes']['memory'].replace(" GiB", "")),
        'pricePerUnit': float(price),
        'instanceType': pricing_data['product']['attributes']['instanceType'],
        'databaseEngine': pricing_data['product']['attributes']['databaseEngine'],
        'deploymentOption': pricing_data['product']['attributes']['deploymentOption']
    }

def get_cloudwatch_metrics(cw_client, db_id: str, metric_name: str, 
                          start_time: datetime, end_time: datetime) -> pd.DataFrame:
    """
    Fetch CloudWatch metrics for an RDS instance.
    
    Args:
        cw_client: Boto3 CloudWatch client
        db_id: RDS instance identifier
        metric_name: CloudWatch metric name
        start_time: Start time for metrics
        end_time: End time for metrics
        
    Returns:
        DataFrame containing metric data
    """
    stats = cw_client.get_metric_statistics(
        Namespace='AWS/RDS',
        Dimensions=[{'Name': 'DBInstanceIdentifier', 'Value': db_id}],
        MetricName=metric_name,
        StartTime=start_time,
        EndTime=end_time,
        Period=3600,
        Statistics=['Maximum']
    )
    
    df = pd.DataFrame(stats['Datapoints'])
    if not df.empty:
        df['DBInstanceIdentifier'] = db_id
    return df

def create_instance_dataframe(instances: list) -> pd.DataFrame:
    """
    Create initial dataframe from RDS instances with proper handling of optional fields.
    
    Args:
        instances: List of RDS instance descriptions from boto3
        
    Returns:
        DataFrame with standardized instance information
    """
    processed_instances = []
    
    for instance in instances:
        instance_data = {
            'DBInstanceIdentifier': instance.get('DBInstanceIdentifier', ''),
            'DBInstanceClass': instance.get('DBInstanceClass', ''),
            'Engine': instance.get('Engine', ''),
            'DBInstanceStatus': instance.get('DBInstanceStatus', ''),
            'AllocatedStorage': instance.get('AllocatedStorage', 0),
            'SecondaryAvailabilityZone': instance.get('SecondaryAvailabilityZone', None)
        }
        processed_instances.append(instance_data)
    
    df = pd.DataFrame(processed_instances)
    return df

def main():
    # Initialize AWS clients
    rds_client = boto3.client('rds')
    cw_client = boto3.client('cloudwatch')
    session = boto3.session.Session()
    region = session.region_name

    print(f"Analyzing RDS instances in region: {region}")

    # Get RDS instances
    response = rds_client.describe_db_instances()
    required_engines = ['aurora-mysql', 'mysql', 'aurora-postgresql', 'postgresql']
    instances = [db for db in response['DBInstances'] 
                if db['Engine'] in required_engines 
                and db['DBInstanceClass'] not in ['db.serverless']]

    # Create initial dataframe with proper handling of optional fields
    df = create_instance_dataframe(instances)
    
    # Get Aurora pricing
    ams_acu_price, ams_iops_price = get_aurora_serverless_pricing(region, "Aurora MySQL")
    apg_acu_price, apg_iops_price = get_aurora_serverless_pricing(region, "Aurora PostgreSQL")

    # Get instance pricing and details
    for idx, row in df.iterrows():
        engine_map = {
            'mysql': 'MySQL',
            'aurora-mysql': 'Aurora MySQL',
            'postgresql': 'PostgreSQL',
            'aurora-postgresql': 'Aurora PostgreSQL'
        }
        
        # Determine if instance is Multi-AZ
        is_multi_az = pd.notna(row['SecondaryAvailabilityZone']) and row['SecondaryAvailabilityZone'] != ''
        deployment_option = 'Multi-AZ' if is_multi_az else 'Single-AZ'
        
        database_engine = engine_map.get(row['Engine'], 'MySQL')
        
        try:
            price_info = get_rds_instance_hourly_price(
                region, 
                row['DBInstanceClass'],
                database_engine,
                deployment_option
            )
            
            df.loc[idx, ['pricePerUnit', 'deploymentOption', 'vcpu', 'memory', 'pricePerMonth']] = [
                price_info['pricePerUnit'],
                price_info['deploymentOption'],
                price_info['vcpu'],
                price_info['memory'],
                price_info['pricePerUnit'] * 730
            ]
        except Exception as e:
            print(f"Error getting pricing for {row['DBInstanceIdentifier']}: {str(e)}")
            # Set default values if pricing lookup fails
            df.loc[idx, ['pricePerUnit', 'deploymentOption', 'vcpu', 'memory', 'pricePerMonth']] = [
                0, deployment_option, 0, 0, 0
            ]

    # Get CloudWatch metrics
    end_time = datetime.now()
    start_time = end_time - timedelta(days=14)
    
    metrics = []
    for db_id in df['DBInstanceIdentifier']:
        # CPU Utilization
        cpu_df = get_cloudwatch_metrics(cw_client, db_id, 'CPUUtilization', start_time, end_time)
        if not cpu_df.empty:
            metrics.append({
                'DBInstanceIdentifier': db_id,
                'meanCPU': cpu_df['Maximum'].mean(),
                'maxCPU': cpu_df['Maximum'].max()
            })
        else:
            metrics.append({
                'DBInstanceIdentifier': db_id,
                'meanCPU': 0,
                'maxCPU': 0
            })
        
        # IOPS metrics for non-Aurora engines
        engine = df[df['DBInstanceIdentifier'] == db_id]['Engine'].iloc[0]
        if engine not in ['aurora-mysql', 'aurora-postgresql']:
            read_iops_df = get_cloudwatch_metrics(cw_client, db_id, 'ReadIOPS', start_time, end_time)
            write_iops_df = get_cloudwatch_metrics(cw_client, db_id, 'WriteIOPS', start_time, end_time)
            
            metrics[-1].update({
                'meanReadIOPS': read_iops_df['Maximum'].mean() if not read_iops_df.empty else 0,
                'meanWriteIOPS': write_iops_df['Maximum'].mean() if not write_iops_df.empty else 0
            })
        else:
            metrics[-1].update({
                'meanReadIOPS': 0,
                'meanWriteIOPS': 0
            })

    # Combine metrics with instance data
    metrics_df = pd.DataFrame(metrics)
    df = pd.merge(df, metrics_df, on='DBInstanceIdentifier', how='left')

    # Calculate Aurora Serverless costs
    df['acu_usage'] = df['meanCPU'].fillna(0) * 1.50 * df['vcpu'] * 4 / 100
    df['acu_usage'] = df['acu_usage'].apply(np.floor) + 0.5
    
    df['IOPS'] = (df['meanReadIOPS'].fillna(0) + df['meanWriteIOPS'].fillna(0)) * 1.50
    
    # Calculate final costs
    df['acuPricePerMonth'] = np.where(
        df['deploymentOption'] == 'Single-AZ',
        df['acu_usage'] * ams_acu_price * 730,
        df['acu_usage'] * ams_acu_price * 730 * 2
    )
    
    df['miopsPricePerMonth'] = (df['IOPS'] * 2628000) * ams_iops_price
    df['aurora_serverless_PricePerMonth'] = df['acuPricePerMonth'] + df['miopsPricePerMonth']
    df['potentialSavings'] = df['pricePerMonth'] - df['aurora_serverless_PricePerMonth']

    # Save results
    output_file = f"aurora_serverless_tco_{os.getpid():06d}.csv"
    df.to_csv(output_file, index=False)
    print(f"Results saved to: {output_file}")
    
    # Print summary
    print("\nSummary of potential savings:")
    print(f"Total monthly savings: ${df['potentialSavings'].sum():,.2f}")
    print(f"Number of instances analyzed: {len(df)}")

if __name__ == "__main__":
    main()