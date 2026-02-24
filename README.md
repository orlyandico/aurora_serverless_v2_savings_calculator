# Aurora Serverless v2 Savings Calculator

Estimate potential savings from migrating qualifying databases across all AWS regions to Aurora Serverless V2, comparing Standard, I/O-Optimized, and Savings Plan pricing.

**REQUIRES Python packages: boto3, pandas, numpy**

## Features

- **Multi-region analysis**: Automatically queries all AWS regions with RDS availability
- **Multiple pricing models**: Compares three Aurora Serverless v2 configurations:
  - Standard (with I/O charges)
  - I/O-Optimized (no I/O charges, higher ACU cost)
  - Savings Plan (35% discount on ACU, 1-year commitment)
- **Comprehensive cost analysis**: Identifies the best option for each database
- **CloudWatch metrics**: Uses 14 days of CPU and IOPS data for accurate projections

## How It Works

The script performs the following steps:

1. Discovers all AWS regions with RDS availability
2. For each region:
   - Describes all RDS instances (excludes ServerlessV2 and non-MySQL/PostgreSQL databases)
   - Fetches hourly pricing and specs (vCPU, memory) for each instance
   - Retrieves 14 days of CPU and IOPS metrics from CloudWatch
   - Calculates equivalent ACU usage: `(CPU% / 100) × vCPU × 4`
   - Computes monthly costs for all three Aurora Serverless v2 configurations
3. Combines results across all regions
4. Identifies the best pricing option for each database
5. Generates comprehensive CSV report with savings analysis

## Pricing Models Compared

### Standard
- ACU: On-demand hourly rate
- I/O: $0.20 per million requests (region-dependent)
- Best for: Low I/O workloads

### I/O-Optimized
- ACU: Higher hourly rate (~25% more than Standard)
- I/O: No charges
- Best for: High I/O workloads (>15% of compute cost)

### Savings Plan
- ACU: 35% discount on Standard rate
- I/O: Same as Standard ($0.20 per million requests)
- Commitment: 1-year, $/hour commitment
- Best for: Predictable workloads with cost optimization priority

## Usage

```bash
python3 aurora_serverlessv2_savings_calculator.py
```

Requires AWS credentials configured via `aws configure` with permissions for:
- `rds:DescribeDBInstances` (all regions)
- `cloudwatch:GetMetricStatistics` (all regions)
- `pricing:GetProducts` (us-east-1)
- `ec2:DescribeRegions`

## Output

CSV file: `aurora_serverless_analysis_YYYYMMDD_HHMMSS.csv`

Columns include:
- Instance details (ID, class, engine, region)
- Current pricing (hourly, monthly)
- CloudWatch metrics (CPU, IOPS)
- ACU usage calculation
- Monthly costs for all three options
- Savings for each option
- Best option recommendation
- Maximum potential savings

Console summary shows:
- Total instances analyzed
- Regions with instances
- Current monthly cost
- Potential savings by option
- Best option distribution

## Version Compatibility

The script identifies instances requiring engine upgrades for Serverless v2 compatibility:

**Aurora MySQL:**
- Requires: Version 3.x
- Extended support: Version 2.x (MySQL 5.7)

**Aurora PostgreSQL:**
- Requires: 13.6+, 14.3+, or 15.2+
- Extended support: Versions 11, 12, 13

Instances flagged with `NeedsUpgrade=True` or `ExtendedSupport=True` require migration planning.

**Hardcoded version thresholds:** Update constants at top of script as AWS requirements change:
- `AURORA_MYSQL_MIN_VERSION`
- `AURORA_MYSQL_EXTENDED_SUPPORT`
- `AURORA_POSTGRESQL_MIN_VERSIONS`
- `AURORA_POSTGRESQL_EXTENDED_SUPPORT`

## Notes

- Assumes 1 ACU = 0.25 vCPU (4 ACU per vCPU)
- Uses maximum CPU/IOPS from CloudWatch with 1.5x multiplier for headroom
- Assumes all instances are On-Demand (doesn't factor in existing Reserved Instances)
- IOPS metrics only available for RDS EBS engines (MySQL, PostgreSQL)
- Aurora instances: IOPS are cluster-level and identical between provisioned and serverless
- Multi-AZ deployments: ACU costs doubled (2x writer + reader)
- Savings Plan discount: 35% applied to ACU costs only
- Storage costs included: Standard vs I/O-Optimized pricing (I/O-Optimized ~15-20% higher per GB)
