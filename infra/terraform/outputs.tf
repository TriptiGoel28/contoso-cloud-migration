# ============================================================
# Contoso Financial — Terraform Outputs
# ============================================================

output "vpc_id" {
  value       = aws_vpc.main.id
  description = "VPC ID"
}

output "public_subnet_ids" {
  value       = aws_subnet.public[*].id
  description = "Public subnet IDs (ALB lives here)"
}

output "private_subnet_ids" {
  value       = aws_subnet.private[*].id
  description = "Private subnet IDs (ECS, RDS, Redis live here)"
}

output "alb_dns_name" {
  value       = aws_lb.main.dns_name
  description = "ALB DNS name — point your Route 53 CNAME or alias record here"
}

output "alb_zone_id" {
  value       = aws_lb.main.zone_id
  description = "ALB hosted zone ID — used for Route 53 alias records"
}

output "rds_endpoint" {
  value       = aws_db_instance.main.endpoint
  sensitive   = true
  description = "RDS endpoint (host:port) — used to build DATABASE_URL SSM parameter"
}

output "rds_port" {
  value       = aws_db_instance.main.port
  description = "RDS port (5432)"
}

output "redis_endpoint" {
  value       = aws_elasticache_cluster.redis.cache_nodes[0].address
  description = "ElastiCache Redis endpoint — used to build REDIS_URL SSM parameter"
}

output "redis_port" {
  value       = aws_elasticache_cluster.redis.port
  description = "ElastiCache port (6379)"
}

output "ecr_webapp_url" {
  value       = aws_ecr_repository.webapp.repository_url
  description = "ECR URL for web app image — docker push <this>:<tag>"
}

output "ecr_batch_url" {
  value       = aws_ecr_repository.batch.repository_url
  description = "ECR URL for batch worker image — docker push <this>:<tag>"
}

output "ecs_cluster_name" {
  value       = aws_ecs_cluster.main.name
  description = "ECS cluster name"
}

output "ecs_cluster_arn" {
  value       = aws_ecs_cluster.main.arn
  description = "ECS cluster ARN"
}

output "reconciliation_bucket_name" {
  value       = aws_s3_bucket.reconciliation.bucket
  description = "S3 bucket for reconciliation files"
}

output "reconciliation_bucket_arn" {
  value       = aws_s3_bucket.reconciliation.arn
  description = "S3 bucket ARN — used in IAM policies"
}

output "ecs_task_execution_role_arn" {
  value       = aws_iam_role.ecs_task_execution.arn
  description = "ECS task execution role ARN"
}
