# aurora_serverless_v2_savings_calculator
Estimate potential savings from migrating qualifying databases in your current RDS fleet to Aurora Serverless V2

This Jupyter notebook attempts to estimate the potential savings that you can get by migrating qualifying databases in your current RDS fleet (in the current active region) to Aurora Serverless V2.  Note that the usual Python suspects (pandas, numpy, Jupyter) plus the boto3 library must be present, and the AWS environment set up properly (access key, secret key, and region are set via "aws cli").

The notebook performs the following steps:

- describe all RDS instances in the current region; note that ServerlessV2 databases have an instance type of "db.serverless" and ServerlessV1 databases don't appear at all in the describe_db_instances() call
- remove all ServerlessV2 and non (MySQL, PostgreSQL) databases from the list
- get the hourly price and specs (vCPU, memory) for each remaining database
- fetch the last 2 weeks worth of CPU usage data from Cloudwatch for each database,  using the "maximum" parameter, this is in percent
- calculate the mean CPU usage
- merges the database dataframe and cloudwatch dataframe
- calculates the equivalent average ACU using the formula (cpu percentage usage / 100) * vCPU * 4 (based on the rule of thumb that 1 vCPU = 4 ACU)
- calculates the monthly cost for that average ACU, using a hard-wired ACU pricing table (FIXME)
- estimates the "potential savings"
- writes a CSV file with the results

