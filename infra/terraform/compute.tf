# ============================================================
# Contoso Financial — ECS Fargate + ALB
# Web app: 2 tasks behind ALB, private subnets
# Batch worker: 1 task, private subnets, no inbound
# ============================================================

# ---- ECS Cluster ----

resource "aws_ecs_cluster" "main" {
  name = local.name_prefix

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = "${local.name_prefix}-cluster" }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

# ---- ECS Task Definition: Web App ----

resource "aws_ecs_task_definition" "webapp" {
  family                   = "contoso-webapp"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.webapp_cpu
  memory                   = var.webapp_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "webapp"
      image     = "${aws_ecr_repository.webapp.repository_url}:${var.webapp_image_tag}"
      essential = true

      portMappings = [
        {
          containerPort = 8080
          protocol      = "tcp"
        }
      ]

      # Secrets fetched from SSM Parameter Store at container startup
      # No plaintext credentials in task definition
      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/contoso/${var.environment}/database-url"
        },
        {
          name      = "REDIS_URL"
          valueFrom = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/contoso/${var.environment}/redis-url"
        },
        {
          name      = "SECRET_KEY"
          valueFrom = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/contoso/${var.environment}/secret-key"
        }
      ]

      environment = [
        { name = "LOG_LEVEL", value = "INFO" }
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 15
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.webapp.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = { Name = "contoso-webapp-task" }
}

# ---- ECS Task Definition: Batch Worker ----

resource "aws_ecs_task_definition" "batch" {
  family                   = "contoso-batch"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.batch_cpu
  memory                   = var.batch_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "batch-worker"
      image     = "${aws_ecr_repository.batch.repository_url}:${var.batch_image_tag}"
      essential = true

      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/contoso/${var.environment}/database-url"
        }
      ]

      environment = [
        # In AWS production, use IAM task role for S3 access — no keys needed
        # These are only populated for local/hybrid environments
        { name = "INPUT_BUCKET",   value = var.reconciliation_bucket_name },
        { name = "OUTPUT_BUCKET",  value = var.reconciliation_bucket_name },
        { name = "POLL_INTERVAL",  value = "30" },
        { name = "LOG_LEVEL",      value = "INFO" }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.batch.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = { Name = "contoso-batch-task" }
}

data "aws_caller_identity" "current" {}

# ---- ALB ----

resource "aws_lb" "main" {
  name                       = "${local.name_prefix}-alb"
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  enable_deletion_protection = false # Set true before production go-live

  tags = { Name = "${local.name_prefix}-alb" }
}

resource "aws_lb_target_group" "webapp" {
  name        = "${local.name_prefix}-webapp-tg"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip" # Required for Fargate

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }

  tags = { Name = "${local.name_prefix}-webapp-tg" }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.webapp.arn
  }
}

# ---- ECS Service: Web App ----

resource "aws_ecs_service" "webapp" {
  name            = "contoso-webapp"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.webapp.arn
  desired_count   = var.webapp_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.webapp.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.webapp.arn
    container_name   = "webapp"
    container_port   = 8080
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  deployment_controller {
    type = "ECS"
  }

  depends_on = [aws_lb_listener.http]

  tags = { Name = "contoso-webapp-service" }
}

# ---- ECS Service: Batch Worker ----

resource "aws_ecs_service" "batch" {
  name            = "contoso-batch"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.batch.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.batch.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = { Name = "contoso-batch-service" }
}
