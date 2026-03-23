# ============================================================
# Contoso Financial — Main Terraform Configuration
#
# Architecture: ECS Fargate + RDS Postgres 15 + ElastiCache Redis 7
#               + S3 + ALB + CloudWatch + SSM Parameter Store
#
# To initialize and apply:
#   terraform init
#   terraform plan -out=tfplan
#   terraform apply tfplan
#
# Remote state (uncomment and configure before first apply):
# terraform {
#   backend "s3" {
#     bucket         = "contoso-terraform-state-<account-id>"
#     key            = "prod/terraform.tfstate"
#     region         = "us-east-1"
#     encrypt        = true
#     dynamodb_table = "contoso-terraform-locks"
#   }
# }
# ============================================================

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = var.tags
  }
}

locals {
  name_prefix = "${var.project}-${var.environment}"
}

# ---- ECR Repositories ----

resource "aws_ecr_repository" "webapp" {
  name                 = "contoso-webapp"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "contoso-webapp" }
}

resource "aws_ecr_repository" "batch" {
  name                 = "contoso-batch"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "contoso-batch" }
}

# ---- CloudWatch Log Groups ----

resource "aws_cloudwatch_log_group" "webapp" {
  name              = "/ecs/contoso-webapp"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "batch" {
  name              = "/ecs/contoso-batch"
  retention_in_days = 30
}

# ---- S3 Bucket for Reconciliation Files ----

resource "aws_s3_bucket" "reconciliation" {
  bucket = var.reconciliation_bucket_name
}

resource "aws_s3_bucket_versioning" "reconciliation" {
  bucket = aws_s3_bucket.reconciliation.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reconciliation" {
  bucket = aws_s3_bucket.reconciliation.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "reconciliation" {
  bucket                  = aws_s3_bucket.reconciliation.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "reconciliation" {
  bucket = aws_s3_bucket.reconciliation.id

  rule {
    id     = "archive-processed-inputs"
    status = "Enabled"

    filter {
      prefix = "reconciliation-input/processed/"
    }

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = 365
    }
  }

  rule {
    id     = "archive-output-summaries"
    status = "Enabled"

    filter {
      prefix = "reconciliation-output/"
    }

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = 365
    }
  }
}

# ---- IAM: ECS Task Execution Role (pulls images, writes logs, reads SSM) ----

data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "${local.name_prefix}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "ssm_read" {
  statement {
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${var.aws_region}:*:parameter/contoso/*"]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "ecs_execution_ssm" {
  name   = "ssm-read"
  role   = aws_iam_role.ecs_task_execution.id
  policy = data.aws_iam_policy_document.ssm_read.json
}

# ---- IAM: ECS Task Role (app permissions — S3 access) ----

resource "aws_iam_role" "ecs_task" {
  name               = "${local.name_prefix}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

data "aws_iam_policy_document" "s3_reconciliation" {
  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:GetBucketLocation"
    ]
    resources = [
      aws_s3_bucket.reconciliation.arn,
      "${aws_s3_bucket.reconciliation.arn}/*"
    ]
  }
}

resource "aws_iam_role_policy" "ecs_task_s3" {
  name   = "s3-reconciliation"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.s3_reconciliation.json
}
