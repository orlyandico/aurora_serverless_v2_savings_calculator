#!/usr/bin/env python3

"""
Aurora Serverless Cost Calculator

Calculates potential savings from moving existing RDS fleet to Aurora Serverless V2.
Compares Standard, I/O-Optimized, and Savings Plan pricing across all regions.

Notes:
- Assumes 1 ACU = 0.25 vCPU
- Assumes all instances are On-Demand (doesn't factor in Reserved Instances)
- Limited support for burstable instances
- Queries all AWS regions with RDS availability
- Requires properly configured AWS CLI with appropriate permissions

Author: Adapted from original by orly.andico@gmail.com
"""

import warnings
warnings.filterwarnings('ignore', message='.*Boto3 will no longer support Python.*')

import boto3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import os
from typing import Tuple, Dict, Any, List

# Constants
SAVINGS_PLAN_DISCOUNT = 0.35  # 35% discount on ACU costs
HEADROOM_MULTIPLIER = 1.50    # 50% headroom for peak usage
HOURS_PER_MONTH = 730         # Average hours per month
SECONDS_PER_MONTH = 2628000   # 730 hours * 3600 seconds
ACU_PER_VCPU = 4              # 1 ACU = 0.25 vCPU

# Version compatibility thresholds (hardcoded - update as AWS requirements change)
AURORA_MYSQL_MIN_VERSION = '3.'           # Requires 3.x for Serverless v2
AURORA_MYSQL_EXTENDED_SUPPORT = '2.'      # 2.x (MySQL 5.7) in extended support

AURORA_POSTGRESQL_MIN_VERSIONS = {        # Minimum minor versions per major
    '13': '13.6',
    '14': '14.3',
    '15': '15.2'
}
AURORA_POSTGRESQL_EXTENDED_SUPPORT = [11, 12, 13]  # Major versions in extended support

def get_all_rds_regions() -> List[str]:
    """
    Get all AWS regions where RDS is available.
    
    Returns:
        List of region names
    """
    ec2_client = boto3.client('ec2', region_name='us-east-1')
    regions = ec2_client.describe_regions()
    return [region['RegionName'] for region in regions['Regions']]

def get_aurora_serverless_pricing(region_name: str, database_engine: str, 
                                 storage_type: str = 'standard') -> Tuple[float, float, float, float]:
    """
    Get ACU, IOPS, storage, and Savings Plan pricing for Aurora MySQL/PostgreSQL.
    
    Args:
        region_name: AWS region name
        database_engine: Either "Aurora MySQL" or "Aurora PostgreSQL"
        storage_type: Either 'standard' or 'io-optimized'
        
    Returns:
        Tuple of (ACU price, IOPS price, Savings Plan ACU price, Storage price per GB)
    """
    pricing_client = boto3.client('pricing', region_name='us-east-1')

    # Get storage cost
    storage_filters = [
        {'Type': 'TERM_MATCH', 'Field': 'regionCode', 'Value': region_name},
        {'Type': 'TERM_MATCH', 'Field': 'productFamily', 'Value': 'Database Storage'},
        {'Type': 'TERM_MATCH', 'Field': 'databaseEngine', 'Value': database_engine},
        {'Type': 'TERM_MATCH', 'Field': 'deploymentOption', 'Value': 'Serverless'}
    ]
    
    if storage_type == 'io-optimized':
        storage_filters.append({'Type': 'TERM_MATCH', 'Field': 'storageConfiguration', 'Value': 'Aurora I/O-Optimized'})
    
    price_storage = 0.0
    try:
        storage_response = pricing_client.get_products(ServiceCode='AmazonRDS', Filters=storage_filters, MaxResults=1)
        if storage_response['PriceList']:
            storage_pricing = json.loads(storage_response['PriceList'][0])
            storage_terms = storage_pricing['terms']['OnDemand']
            storage_id1 = list(storage_terms)[0]
            storage_id2 = list(storage_terms[storage_id1]['priceDimensions'])[0]
            price_storage = float(storage_terms[storage_id1]['priceDimensions'][storage_id2]['pricePerUnit']['USD'])
    except Exception as e:
        print(f"Warning: Could not get storage pricing for {region_name}: {e}")

    # Get IO operation cost (only for standard storage)
    price_iops = 0.0
    if storage_type == 'standard':
        io_filters = [
            {'Type': 'TERM_MATCH', 'Field': 'regionCode', 'Value': region_name},
            {'Type': 'TERM_MATCH', 'Field': 'productFamily', 'Value': 'System Operation'},
            {'Type': 'TERM_MATCH', 'Field': 'databaseEngine', 'Value': 'Any'},
            {'Type': 'TERM_MATCH', 'Field': 'group', 'Value': 'Aurora I/O Operation'}
        ]
        try:
            io_response = pricing_client.get_products(ServiceCode='AmazonRDS', Filters=io_filters, MaxResults=1)
            if io_response['PriceList']:
                io_pricing = json.loads(io_response['PriceList'][0])
                io_terms = io_pricing['terms']['OnDemand']
                io_id1 = list(io_terms)[0]
                io_id2 = list(io_terms[io_id1]['priceDimensions'])[0]
                price_iops = float(io_terms[io_id1]['priceDimensions'][io_id2]['pricePerUnit']['USD'])
        except Exception as e:
            print(f"Warning: Could not get IOPS pricing for {region_name}: {e}")

    # Get ACU cost
    acu_filters = [
        {'Type': 'TERM_MATCH', 'Field': 'regionCode', 'Value': region_name},
        {'Type': 'TERM_MATCH', 'Field': 'databaseEngine', 'Value': database_engine},
        {'Type': 'TERM_MATCH', 'Field': 'productFamily', 'Value': 'ServerlessV2'}
    ]
    
    # Add storage configuration filter for I/O-Optimized
    if storage_type == 'io-optimized':
        acu_filters.append({'Type': 'TERM_MATCH', 'Field': 'storageConfiguration', 'Value': 'Aurora I/O-Optimized'})
    
    try:
        acu_response = pricing_client.get_products(ServiceCode='AmazonRDS', Filters=acu_filters, MaxResults=1)
        if not acu_response['PriceList']:
            return 0.0, 0.0, 0.0, 0.0
            
        acu_pricing = json.loads(acu_response['PriceList'][0])
        
        # Get On-Demand pricing
        acu_terms = acu_pricing['terms']['OnDemand']
        acu_id1 = list(acu_terms)[0]
        acu_id2 = list(acu_terms[acu_id1]['priceDimensions'])[0]
        price_acu = float(acu_terms[acu_id1]['priceDimensions'][acu_id2]['pricePerUnit']['USD'])
        
        # Get Savings Plan pricing (35% discount)
        price_acu_sp = price_acu * (1 - SAVINGS_PLAN_DISCOUNT)
        
        return price_acu, price_iops, price_acu_sp, price_storage
    except Exception as e:
        print(f"Warning: Could not get ACU pricing for {region_name}, {database_engine}, {storage_type}: {e}")
        return 0.0, 0.0, 0.0, 0.0

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
        engine = instance.get('Engine', '')
        version = instance.get('EngineVersion', '')
        
        # Check if version is compatible with Serverless v2
        needs_upgrade = False
        extended_support = False
        
        if engine == 'aurora-mysql':
            needs_upgrade = not version.startswith(AURORA_MYSQL_MIN_VERSION)
            if version.startswith(AURORA_MYSQL_EXTENDED_SUPPORT):
                extended_support = True
        elif engine == 'aurora-postgresql':
            major = int(version.split('.')[0])
            major_minor = '.'.join(version.split('.')[:2])
            
            # Check minimum version requirements
            if str(major) in AURORA_POSTGRESQL_MIN_VERSIONS:
                needs_upgrade = major_minor < AURORA_POSTGRESQL_MIN_VERSIONS[str(major)]
            else:
                needs_upgrade = True  # Major version not in supported list
            
            # Check extended support
            if major in AURORA_POSTGRESQL_EXTENDED_SUPPORT:
                extended_support = True
        
        instance_data = {
            'DBInstanceIdentifier': instance.get('DBInstanceIdentifier', ''),
            'DBInstanceClass': instance.get('DBInstanceClass', ''),
            'Engine': engine,
            'EngineVersion': version,
            'NeedsUpgrade': needs_upgrade,
            'ExtendedSupport': extended_support,
            'DBInstanceStatus': instance.get('DBInstanceStatus', ''),
            'AllocatedStorage': instance.get('AllocatedStorage', 0),
            'SecondaryAvailabilityZone': instance.get('SecondaryAvailabilityZone', None)
        }
        processed_instances.append(instance_data)
    
    df = pd.DataFrame(processed_instances)
    return df

def process_region(region: str) -> pd.DataFrame:
    """
    Process all RDS instances in a single region.
    
    Args:
        region: AWS region name
        
    Returns:
        DataFrame with cost analysis for the region
    """
    print(f"\nAnalyzing region: {region}")
    
    try:
        rds_client = boto3.client('rds', region_name=region)
        cw_client = boto3.client('cloudwatch', region_name=region)
        
        # Get RDS instances
        response = rds_client.describe_db_instances()
        required_engines = ['aurora-mysql', 'mysql', 'aurora-postgresql', 'postgresql']
        instances = [db for db in response['DBInstances'] 
                    if db['Engine'] in required_engines 
                    and db['DBInstanceClass'] not in ['db.serverless']]
        
        if not instances:
            print(f"  No qualifying instances found in {region}")
            return pd.DataFrame()
        
        print(f"  Found {len(instances)} instances")
        
        # Create initial dataframe
        df = create_instance_dataframe(instances)
        df['Region'] = region
        
        # Get Aurora pricing for all configurations
        pricing_configs = {
            'standard': {},
            'io_optimized': {},
            'savings_plan': {}
        }
        
        for engine in ['Aurora MySQL', 'Aurora PostgreSQL']:
            # Standard pricing
            acu_std, iops_std, acu_sp, storage_std = get_aurora_serverless_pricing(region, engine, 'standard')
            pricing_configs['standard'][engine] = {'acu': acu_std, 'iops': iops_std, 'storage': storage_std}
            pricing_configs['savings_plan'][engine] = {'acu': acu_sp, 'iops': iops_std, 'storage': storage_std}
            
            # I/O-Optimized pricing
            acu_io, _, acu_io_sp, storage_io = get_aurora_serverless_pricing(region, engine, 'io-optimized')
            pricing_configs['io_optimized'][engine] = {'acu': acu_io, 'iops': 0.0, 'storage': storage_io}
        
        # Get instance pricing and details
        for idx, row in df.iterrows():
            engine_map = {
                'mysql': 'MySQL',
                'aurora-mysql': 'Aurora MySQL',
                'postgresql': 'PostgreSQL',
                'aurora-postgresql': 'Aurora PostgreSQL'
            }
            
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
                    price_info['pricePerUnit'] * HOURS_PER_MONTH
                ]
            except Exception as e:
                print(f"  Warning: Could not get pricing for {row['DBInstanceIdentifier']}: {e}")
                df.loc[idx, ['pricePerUnit', 'deploymentOption', 'vcpu', 'memory', 'pricePerMonth']] = [
                    0, deployment_option, 0, 0, 0
                ]
        
        # Get CloudWatch metrics
        end_time = datetime.now()
        start_time = end_time - timedelta(days=14)
        
        metrics = []
        for db_id in df['DBInstanceIdentifier']:
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
        
        metrics_df = pd.DataFrame(metrics)
        df = pd.merge(df, metrics_df, on='DBInstanceIdentifier', how='left')
        
        # Calculate Aurora Serverless costs for all configurations
        # ACU usage = (CPU% / 100) × vCPU × ACU_PER_VCPU × HEADROOM_MULTIPLIER
        df['acu_usage'] = df['meanCPU'].fillna(0) * HEADROOM_MULTIPLIER * df['vcpu'] * ACU_PER_VCPU / 100
        df['acu_usage'] = df['acu_usage'].apply(np.floor) + 0.5
        df['IOPS'] = (df['meanReadIOPS'].fillna(0) + df['meanWriteIOPS'].fillna(0)) * HEADROOM_MULTIPLIER
        
        # Calculate costs for each configuration
        for idx, row in df.iterrows():
            aurora_engine = 'Aurora MySQL' if 'mysql' in row['Engine'] else 'Aurora PostgreSQL'
            multi_az_multiplier = 2 if row['deploymentOption'] == 'Multi-AZ' else 1
            storage_gb = row['AllocatedStorage']
            
            # Standard
            std_pricing = pricing_configs['standard'][aurora_engine]
            acu_cost_std = row['acu_usage'] * std_pricing['acu'] * HOURS_PER_MONTH * multi_az_multiplier
            iops_cost_std = (row['IOPS'] * SECONDS_PER_MONTH) * std_pricing['iops']
            storage_cost_std = storage_gb * std_pricing['storage']
            df.loc[idx, 'aurora_standard_monthly'] = acu_cost_std + iops_cost_std + storage_cost_std
            
            # I/O-Optimized
            io_pricing = pricing_configs['io_optimized'][aurora_engine]
            acu_cost_io = row['acu_usage'] * io_pricing['acu'] * HOURS_PER_MONTH * multi_az_multiplier
            storage_cost_io = storage_gb * io_pricing['storage']
            df.loc[idx, 'aurora_io_optimized_monthly'] = acu_cost_io + storage_cost_io
            
            # Savings Plan
            sp_pricing = pricing_configs['savings_plan'][aurora_engine]
            acu_cost_sp = row['acu_usage'] * sp_pricing['acu'] * HOURS_PER_MONTH * multi_az_multiplier
            iops_cost_sp = (row['IOPS'] * SECONDS_PER_MONTH) * sp_pricing['iops']
            storage_cost_sp = storage_gb * sp_pricing['storage']
            df.loc[idx, 'aurora_savings_plan_monthly'] = acu_cost_sp + iops_cost_sp + storage_cost_sp
        
        # Calculate savings
        df['savings_standard'] = df['pricePerMonth'] - df['aurora_standard_monthly']
        df['savings_io_optimized'] = df['pricePerMonth'] - df['aurora_io_optimized_monthly']
        df['savings_plan'] = df['pricePerMonth'] - df['aurora_savings_plan_monthly']
        
        # Determine best option
        df['best_option'] = df[['aurora_standard_monthly', 'aurora_io_optimized_monthly', 
                                'aurora_savings_plan_monthly']].idxmin(axis=1)
        df['best_option'] = df['best_option'].str.replace('aurora_', '').str.replace('_monthly', '')
        df['max_savings'] = df[['savings_standard', 'savings_io_optimized', 'savings_plan']].max(axis=1)
        
        return df
        
    except Exception as e:
        print(f"  Error processing region {region}: {e}")
        return pd.DataFrame()

def main():
    print("Aurora Serverless v2 Multi-Region Cost Calculator")
    print("=" * 60)
    
    # Get all regions
    regions = get_all_rds_regions()
    print(f"\nFound {len(regions)} AWS regions")
    
    # Process each region
    all_results = []
    for region in regions:
        df = process_region(region)
        if not df.empty:
            all_results.append(df)
    
    if not all_results:
        print("\nNo RDS instances found in any region")
        return
    
    # Combine all results
    combined_df = pd.concat(all_results, ignore_index=True)
    
    # Save results
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f"aurora_serverless_analysis_{timestamp}.csv"
    combined_df.to_csv(output_file, index=False)
    print(f"\n{'=' * 60}")
    print(f"Results saved to: {output_file}")
    
    # Print summary
    print(f"\n{'SUMMARY':^60}")
    print("=" * 60)
    print(f"Total instances analyzed: {len(combined_df)}")
    print(f"Regions with instances: {combined_df['Region'].nunique()}")
    print(f"\nTotal current monthly cost: ${combined_df['pricePerMonth'].sum():,.2f}")
    print(f"\nPotential monthly savings by option:")
    print(f"  Standard:        ${combined_df['savings_standard'].sum():,.2f}")
    print(f"  I/O-Optimized:   ${combined_df['savings_io_optimized'].sum():,.2f}")
    print(f"  Savings Plan:    ${combined_df['savings_plan'].sum():,.2f}")
    print(f"\nMaximum savings:   ${combined_df['max_savings'].sum():,.2f}")
    print(f"\nBest option distribution:")
    print(combined_df['best_option'].value_counts().to_string())
    print("=" * 60)

if __name__ == "__main__":
    main()