# Terraform — AWS EKS target (not applied in v1)

This provisions the production AWS footprint from `instructions.md`: VPC, EKS,
managed nodes, ECR repos, and an ECR push IAM policy. **v1 runs on local kind**,
so this is not applied — it's the faithful IaC for the AWS migration.

## When you move to AWS

```bash
cd infra/terraform
terraform init
terraform plan
terraform apply        # needs AWS credentials; costs real money
```

Then:
1. `aws eks update-kubeconfig --name ai-platform`
2. Push images to the ECR URLs (`terraform output ecr_repository_urls`).
3. Repoint the Helm `image.repository` values and ArgoCD `repoURL` at the cluster.
4. Install the AWS Load Balancer Controller for real ALB ingress.

## Not covered here (deliberately)
- AWS Load Balancer Controller install (in-cluster Helm, not Terraform).
- ArgoCD/monitoring install — same Helm/manifests as local, just a different cluster.
