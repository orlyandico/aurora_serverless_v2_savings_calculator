# aurora_serverless_v2_savings_calculator
Estimate potential savings from migrating qualifying databases in your current RDS fleet in the currently defined region to Aurora Serverless V2

This Jupyter notebook attempts to estimate the potential savings that you can get by migrating qualifying databases in your current RDS fleet (in the current active region) to Aurora Serverless V2.  Note that the usual Python suspects (pandas, numpy, Jupyter) plus the boto3 library must be present, and the AWS environment set up properly (access key, secret key, and region are set via "aws cli").

The notebook performs the following steps:

- describe all RDS instances in the current region; note that ServerlessV2 databases have an instance type of "db.serverless" and ServerlessV1 databases don't appear at all in the describe_db_instances() call
- remove all ServerlessV2 and non (MySQL, PostgreSQL) databases from the list
- get the hourly price and specs (vCPU, memory) for each remaining database
- fetch the last 2 weeks worth of CPU, Read IOPS, and Write IOPS usage data from Cloudwatch for each database,  using the "maximum" parameter, this is in percent
- calculate the mean CPU usage, and mean IOPS (Read + Write); IOPS is only available for RDS EBS engines (long version: IOPS are only available at cluster level for Aurora databases, and any IOPS on Aurora Provisioned vs Serverless would be the same and so would cancel out)
- merges the database dataframe and cloudwatch dataframe
- calculates the equivalent average ACU using the formula (cpu percentage usage / 100) * vCPU * 4 (based on the rule of thumb that 1 vCPU = 4 ACU)
- calculates the monthly cost for that average ACU, using the pricing API (currently hardwired to use Aurora PostgreSQL ACU, which is the same as Aurora MySQL ACU so this is OK for now)
- calculates the cost of Aurora IOPS based on the Read+Write IOPS average multiplied by seconds per month, also fetching IOPS cost via pricing API
- estimates the "potential savings" inclusive of IOPS cost
- writes a CSV file with the results

