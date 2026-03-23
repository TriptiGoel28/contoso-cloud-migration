# ============================================================
# Contoso Financial — Data Layer: RDS, ElastiCache, SSM
# ============================================================

# ---- RDS Subnet Group ----

resource "aws_db_subnet_group" "main" {
  name        = "${local.name_prefix}-db-subnet-group"
  subnet_ids  = aws_subnet.private[*].id
  description = "Private subnets for RDS — no public access"

  tags = { Name = "${local.name_prefix}-db-subnet-group" }
}

# ---- RDS Postgres 15 ----

resource "aws_db_instance" "main" {
  identifier     = "${local.name_prefix}-db"
  engine         = "postgres"
  engine_version = "15.4"
  instance_class = var.db_instance_class

  db_name  = var.db_name
  username = var.db_username

  # AWS manages the master password and rotates it automatically via Secrets Manager
  manage_master_user_password = true

  # Storage
  allocated_storage     = 20
  max_allocated_storage = 100
  storage_type          = "gp3"
  storage_encrypted     = true

  # HA
  multi_az = var.db_multi_az

  # Backups
  backup_retention_period = 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "Mon:04:00-Mon:05:00"
  copy_tags_to_snapshot   = true

  # Network
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false

  # Protection
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name_prefix}-final-snapshot"

  # Performance
  performance_insights_enabled = true

  # TODO: The internal ledger service (10.0.1.45 / ledger-svc-old.contoso.internal)
  # that the web app calls on every transaction POST is NOT provisioned here.
  # This is the undocumented dependency found in Discovery (docs/02-discovery.md).
  # Ownership: unknown. Last deployed: 2021.
  # Action required BEFORE cutover:
  #   1. Identify service owner (try: grep the on-prem CMDB, ask the Platform team)
  #   2. Document the API contract (/ledger/api/v1/balance)
  #   3. Decision: migrate alongside, proxy via API Gateway stub, or mock with fallback
  # This gap is intentional — IaC cannot provision what hasn't been identified.

  tags = { Name = "${local.name_prefix}-db" }
}

# ---- ElastiCache Subnet Group ----

resource "aws_elasticache_subnet_group" "main" {
  name        = "${local.name_prefix}-redis-subnet-group"
  subnet_ids  = aws_subnet.private[*].id
  description = "Private subnets for ElastiCache"
}

# ---- ElastiCache Redis 7 ----

resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "${local.name_prefix}-redis"
  engine               = "redis"
  node_type            = var.redis_node_type
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  port                 = 6379

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  # Enable at-rest encryption
  at_rest_encryption_enabled = true

  tags = { Name = "${local.name_prefix}-redis" }
}

# ---- SSM Parameters ----
# These hold runtime config fetched by ECS containers at startup.
# Values are placeholders — populated by the deployment pipeline after infrastructure creation.
# Use: aws ssm put-parameter --name <name> --value <value> --type SecureString --overwrite

resource "aws_ssm_parameter" "database_url" {
  name        = "/contoso/${var.environment}/database-url"
  type        = "SecureString"
  value       = "PLACEHOLDER — set after RDS creation: postgresql://${var.db_username}:<password>@${aws_db_instance.main.endpoint}/${var.db_name}"
  description = "RDS connection string for ECS tasks"

  lifecycle {
    # Prevent Terraform from overwriting a real value set by the deployment pipeline
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "redis_url" {
  name        = "/contoso/${var.environment}/redis-url"
  type        = "SecureString"
  value       = "PLACEHOLDER — set after ElastiCache creation: redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0"
  description = "ElastiCache connection string for ECS tasks"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "secret_key" {
  name        = "/contoso/${var.environment}/secret-key"
  type        = "SecureString"
  # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
  value       = "PLACEHOLDER — generate a random 32-byte hex string and set this manually"
  description = "Flask SECRET_KEY — must be a random 32+ byte value"

  lifecycle {
    ignore_changes = [value]
  }
}
