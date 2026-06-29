output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "region" {
  value = var.region
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "ecr_repository_urls" {
  value = { for name, r in aws_ecr_repository.repos : name => r.repository_url }
}

output "ecr_push_policy_arn" {
  value = aws_iam_policy.ecr_push.arn
}
