variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "europe-west1"
}

variable "zone" {
  type    = string
  default = "europe-west1-b"
}

variable "name" {
  type    = string
  default = "yt2mf"
}

variable "repo_url" {
  type        = string
  description = "Public Git repository URL containing this project."
}

variable "machine_type_main" {
  type    = string
  default = "e2-standard-2"
}

variable "machine_type_worker" {
  type    = string
  default = "c3-standard-4"
}

variable "worker_min" {
  type    = number
  default = 0
}

variable "worker_max" {
  type    = number
  default = 10
}

variable "db_name" {
  type    = string
  default = "yt2mf"
}

variable "db_user" {
  type    = string
  default = "yt2mf"
}

variable "db_password" {
  type        = string
  sensitive   = true
  nullable    = true
  default     = null
  description = "Optional Cloud SQL password. If omitted, Terraform generates one."
}

variable "bot_token" {
  type      = string
  sensitive = true
}

variable "pubsub_topic" {
  type    = string
  default = "video-jobs"
}

variable "pubsub_subscription" {
  type    = string
  default = "video-workers"
}

variable "worker_image" {
  type    = string
  default = "ubuntu-2404-lts-amd64"
}
