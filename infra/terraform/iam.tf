# Cluster + node IAM roles are created by the EKS module. This adds a CI push
# policy for ECR (attach to the GitHub Actions OIDC role / CI user).

data "aws_iam_policy_document" "ecr_push" {
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid    = "EcrPush"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [for r in aws_ecr_repository.repos : r.arn]
  }
}

resource "aws_iam_policy" "ecr_push" {
  name   = "${var.cluster_name}-ecr-push"
  policy = data.aws_iam_policy_document.ecr_push.json
}
