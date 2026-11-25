# Costing & Budget

Demo load: 5 vehicles x 1 message every 2 s = 2.5 msg/s, about **6.5 million messages / month** if run 24x7.
Prices: us-east-1, Nov 2026. Learner Lab accounts are paid from your lab credit (no Free Tier),
so the last column shows what a normal AWS account would pay after the *always-free* limits
(AWS replaced most 12-month free offers with sign-up credits in July 2025, so only always-free limits are counted).

| Service | Usage (24x7 demo load) | Approx. monthly cost | After always-free limits |
|---|---|---|---|
| Kinesis Data Streams | 1 provisioned shard ($0.015/shard-hour) + PUT units | **~$11.2** | ~$11.2 (no free tier) |
| AWS IoT Core | 6.5 M messages ($1.00/M) + rule + Kinesis action (~$0.30/M) + 1 connection | ~$8.6 | ~$8.6 |
| AWS Lambda | ~4 M invocations equivalent, 256 MB, ~50 ms | ~$1.2 | $0 (1 M req + 400k GB-s free) |
| DynamoDB (on-demand) | ~6.5 M writes, ~6.5 M reads, < 1 GB | ~$5 | ~$4 (25 GB storage free) |
| API Gateway WebSocket | messages + connection minutes (few dashboards) | ~$1 | ~$1 |
| API Gateway HTTP API | few thousand requests | < $0.01 | $0 |
| SNS email | a few hundred emails | $0 | $0 (1,000 emails free) |
| S3 + CloudFront | < 1 MB site, little traffic | < $0.10 | $0 (CloudFront 1 TB always free) |
| CloudWatch | logs (7-day retention), 3 alarms, 1 dashboard | ~$1-3 | ~$0-3 |
| **Total (24x7)** | | **~$28 / month** | **~$25 / month** |

## Realistic student usage (what you will actually spend)
You only run the simulator during testing and the demo (for example 3 hours a day, 10 days):
* Kinesis: the shard is billed while it **exists**, so about $0.36 per day -> **~$3.6 for 10 days**.
* Everything else scales with messages, about 270k messages in total -> **under $1**.
* **Expected total: about $4-5 of Learner Lab credit.**

## Cost optimisations used
1. Serverless everywhere: nothing is billed while idle except the Kinesis shard.
2. DynamoDB TTL deletes telemetry after 7 days, so storage stays small.
3. CloudWatch log retention is 7 days.
4. One WebSocket push per Lambda batch (not per reading) means fewer API Gateway messages.
5. Batched `put_records` calls in the simulator.
6. CloudFront PriceClass_100 (cheapest edge locations).

## Further savings (trade-offs)
* Replace Kinesis with an **IoT Rule -> Lambda** action (or SQS): saves ~$11/month but loses the 24 h replay (RPO).
* **Run `terraform destroy` when you finish each day** to stop the Kinesis shard charge (rebuild later with `terraform apply`).
