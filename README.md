# Aurora Serverless v2 Cost Calculator

Analyses existing RDS and Aurora instances across all AWS regions to calculate potential savings from migrating to Aurora Serverless v2.

## What it does

Compares current provisioned instance costs against four Aurora Serverless v2 pricing models:
- **Standard**: Pay-per-ACU + per-I/O operation charges
- **Standard + Savings Plan**: 35% ACU discount + per-I/O charges
- **I/O-Optimised**: Higher ACU rate, no I/O charges
- **I/O-Optimised + Savings Plan**: 35% ACU discount on I/O-Optimised rates, no I/O charges

## Features

- **Multi-region analysis**: Automatically queries all AWS regions with RDS availability
- **Comprehensive cost analysis**: Identifies the best option for each database
- **CloudWatch metrics**: Uses 14 days of CPU and IOPS data for accurate projections
- **Version compatibility checking**: Flags instances requiring upgrades for Serverless v2
- **Aurora storage handling**: Fetches actual cluster storage from CloudWatch (not instance-level)

## Requirements

- Python 3.x with boto3, pandas, numpy
- AWS CLI configured with credentials
- IAM permissions:
  - `rds:DescribeDBInstances`
  - `cloudwatch:GetMetricStatistics`
  - `pricing:GetProducts`
  - `ec2:DescribeRegions`

## Usage

```bash
python3 aurora_serverlessv2_savings_calculator.py
```

Generates CSV file: `aurora_serverless_analysis_YYYYMMDD_HHMMSS.csv`

Console summary shows:
- Total instances analysed
- Instances with potential savings (excludes negative savings)
- Regions with instances
- Current monthly cost
- Potential savings by option (for profitable migrations only)
- Best option distribution

## Calculations

### How it works

1. Discovers all AWS regions with RDS availability
2. For each region:
   - Describes all RDS instances (excludes Serverless v2 and non-MySQL/PostgreSQL databases)
   - Fetches hourly pricing and specs (vCPU, memory) for each instance
   - Retrieves 14 days of CPU and IOPS metrics from CloudWatch
   - For Aurora: fetches actual storage from cluster-level `VolumeBytesUsed` metric
   - Calculates equivalent ACU usage with 50% headroom
   - Computes monthly costs for all four Aurora Serverless v2 configurations
3. Combines results across all regions
4. Identifies the best pricing option for each database
5. Generates comprehensive CSV report with savings analysis

### ACU sizing

```
ACU = max(0, floor((CPU% / 100) × vCPU × 4 × 1.5) + 0.5)
```

Where:
- CPU% = 14-day average of hourly maximum CPU utilisation
- 4 = ACUs per vCPU (1 ACU = 0.25 vCPU)
- 1.5 = 50% headroom multiplier for peak usage
- 0 = minimum ACU for Aurora Serverless v2

### I/O operations

```
Monthly I/O ops = (avgReadIOPS + avgWriteIOPS) × 2,628,000 × 1.5
```

Where:
- avgReadIOPS/avgWriteIOPS = 14-day average of hourly maximum IOPS
- 2,628,000 = seconds per month (730 hours)
- 1.5 = 50% headroom multiplier

### Monthly costs

**Standard:**
```
Cost = (ACU × ACU_price × 730 × AZ_multiplier) + (I/O_ops × I/O_price) + (Storage_GB × Storage_price)
```

**I/O-Optimised:**
```
Cost = (ACU × ACU_price_IO × 730 × AZ_multiplier) + (Storage_GB × Storage_price_IO)
```

**Standard + Savings Plan:**
```
Cost = (ACU × ACU_price × 0.65 × 730 × AZ_multiplier) + (I/O_ops × I/O_price) + (Storage_GB × Storage_price)
```

**I/O-Optimised + Savings Plan:**
```
Cost = (ACU × ACU_price_IO × 0.65 × 730 × AZ_multiplier) + (Storage_GB × Storage_price_IO)
```

Where:
- 730 = hours per month
- AZ_multiplier = 2 for Multi-AZ, 1 for Single-AZ
- 0.65 = 35% Savings Plan discount

### Storage for Aurora

Aurora instances report `AllocatedStorage=1`. The script fetches actual storage from CloudWatch `VolumeBytesUsed` metric at cluster level.

Aurora storage is replicated across 3 AZs automatically and charged once, regardless of Single-AZ or Multi-AZ deployment.

## Version compatibility

### Aurora MySQL
- **Serverless v2 requires**: 3.x (MySQL 8.0)
- **Extended support**: 2.x (MySQL 5.7)

### Aurora PostgreSQL
- **Serverless v2 minimum versions**:
  - PostgreSQL 13: 13.6+
  - PostgreSQL 14: 14.3+
  - PostgreSQL 15: 15.2+
  - PostgreSQL 16: 16.0+
  - PostgreSQL 17: 17.0+
- **Extended support**: PostgreSQL 11, 12, 13

Instances requiring upgrades are flagged in `NeedsUpgrade` column.

## Output columns

### Instance details
- `DBInstanceIdentifier` - Instance name
- `Region` - AWS region
- `DBInstanceClass` - Instance type
- `Engine` - Database engine
- `EngineVersion` - Version number
- `NeedsUpgrade` - Requires version upgrade for Serverless v2
- `ExtendedSupport` - Version in extended support (additional costs)
- `DBInstanceStatus` - Current state
- `AllocatedStorage` - Storage in GB
- `deploymentOption` - Single-AZ or Multi-AZ
- `vcpu` - vCPU count
- `memory` - RAM in GiB

### Current costs
- `pricePerUnit` - Hourly cost
- `pricePerMonth` - Monthly cost (730 hours)

### Metrics (14-day average)
- `meanCPU` - Average CPU utilisation %
- `maxCPU` - Peak CPU utilisation %
- `meanReadIOPS` - Average read IOPS
- `meanWriteIOPS` - Average write IOPS

### Serverless v2 sizing
- `acu_usage` - Calculated ACU requirement
- `monthly_io_operations` - Total I/O operations per month

### Serverless v2 costs
- `aurora_standard_monthly` - Standard pricing
- `aurora_standard_sp_monthly` - Standard + Savings Plan
- `aurora_io_optimized_monthly` - I/O-Optimised pricing
- `aurora_io_optimized_sp_monthly` - I/O-Optimised + Savings Plan

### Savings analysis
- `savings_standard` - Monthly savings with Standard
- `savings_standard_sp` - Monthly savings with Standard + Savings Plan
- `savings_io_optimized` - Monthly savings with I/O-Optimised
- `savings_io_optimized_sp` - Monthly savings with I/O-Optimised + Savings Plan
- `best_option` - Lowest-cost configuration
- `max_savings` - Maximum monthly savings

## Limitations

- Assumes On-Demand pricing (doesn't account for existing Reserved Instances)
- Uses 14-day CloudWatch metrics (may not capture seasonal patterns)
- 50% headroom may be insufficient for highly variable workloads
- Burstable instances (t2/t3) may show negative savings (Aurora minimum costs exceed tiny instance costs)
- Regular RDS (non-Aurora) instances are costed as Aurora migrations, not like-for-like replacements
- IOPS metrics only available for RDS EBS engines (MySQL, PostgreSQL)
- Aurora instances: IOPS are cluster-level and identical between provisioned and Serverless
- Multi-AZ deployments: ACU costs doubled (2× for writer + reader)

## Important notes

- Assumes 1 ACU = 0.25 vCPU (4 ACU per vCPU)
- Uses maximum CPU/IOPS from CloudWatch with 1.5× multiplier for headroom
- I/O-Optimised: ~36% higher ACU rate, ~15-20% higher storage rate, no I/O charges
- Savings Plan: 35% discount on ACU costs only, 1-year commitment
- Version compatibility thresholds are hardcoded - update constants at top of script as AWS requirements change:
  - `AURORA_MYSQL_MIN_VERSION`
  - `AURORA_MYSQL_EXTENDED_SUPPORT`
  - `AURORA_POSTGRESQL_MIN_VERSIONS`
  - `AURORA_POSTGRESQL_EXTENDED_SUPPORT`

## When I/O-Optimised is cheaper

I/O-Optimised becomes cost-effective when I/O charges exceed the ACU price premium:

```
Break-even: I/O_ops > (ACU × ΔPrice × 730) / I/O_price
```

Typically at 15-20% of monthly costs from I/O operations.
