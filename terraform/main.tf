terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }

    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

# ============================================================
# RANDOM DATABASE PASSWORD
# ============================================================

resource "random_password" "db" {
  count = (
    var.db_password == null ||
    trimspace(var.db_password) == ""
  ) ? 1 : 0

  length  = 32
  special = true
}

# ============================================================
# LOCALS
# ============================================================

locals {
  app = var.name

  common_labels = {
    app        = var.name
    managed_by = "terraform"
  }

  db_password = (
    var.db_password != null &&
    trimspace(var.db_password) != ""
  ) ? var.db_password : random_password.db[0].result
}

# ============================================================
# GOOGLE CLOUD APIs
# ============================================================

resource "google_project_service" "services" {
  for_each = toset([
    "compute.googleapis.com",
    "pubsub.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "monitoring.googleapis.com",
  ])

  service            = each.value
  disable_on_destroy = false
}

# ============================================================
# VPC NETWORK
# ============================================================

resource "google_compute_network" "main" {
  name                    = "${local.app}-network"
  auto_create_subnetworks = true
}

# ============================================================
# FIREWALL - INTERNAL
# ============================================================

resource "google_compute_firewall" "internal" {
  name    = "${local.app}-allow-internal"
  network = google_compute_network.main.name

  allow {
    protocol = "tcp"
    ports    = ["0-65535"]
  }

  allow {
    protocol = "udp"
    ports    = ["0-65535"]
  }

  source_ranges = ["10.128.0.0/9"]
}

# ============================================================
# FIREWALL - WORKER HEALTH / SSH
# ============================================================

resource "google_compute_firewall" "health_check" {
  name    = "${local.app}-allow-health-check"
  network = google_compute_network.main.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = [
    "35.191.0.0/16",
    "130.211.0.0/22",
  ]

  target_tags = ["yt2mf-worker"]
}
resource "google_compute_firewall" "ssh" {
  name    = "${local.app}-allow-ssh"
  network = google_compute_network.main.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["yt2mf-main"]
}
# ============================================================
# PUB/SUB
# ============================================================

resource "google_pubsub_topic" "jobs" {
  name = var.pubsub_topic

  depends_on = [
    google_project_service.services
  ]
}

resource "google_pubsub_subscription" "workers" {
  name  = var.pubsub_subscription
  topic = google_pubsub_topic.jobs.id

  ack_deadline_seconds = 600

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  expiration_policy {
    ttl = ""
  }
}

# ============================================================
# CLOUD STORAGE
# ============================================================

resource "google_storage_bucket" "assets" {
  name     = "${var.project_id}-${var.name}-assets"
  location = var.region

  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    enabled = true
  }

  depends_on = [
    google_project_service.services
  ]
}

# ============================================================
# CLOUD SQL POSTGRESQL
# ============================================================

resource "google_sql_database_instance" "postgres" {
  name             = "${local.app}-postgres"
  database_version = "POSTGRES_17"
  region           = var.region

  deletion_protection = true

  settings {
    tier              = "db-perf-optimized-N-2"
    availability_type = "REGIONAL"

    disk_type       = "PD_SSD"
    disk_size       = 20
    disk_autoresize = true

    backup_configuration {
      enabled = true
    }

    ip_configuration {
      ipv4_enabled = true
    }
  }
  depends_on = [
    google_project_service.services
  ]
}

resource "google_sql_database" "app" {
  name     = var.db_name
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "app" {
  name     = var.db_user
  instance = google_sql_database_instance.postgres.name
  password = local.db_password
}

# ============================================================
# SECRET MANAGER - TELEGRAM BOT TOKEN
# ============================================================

resource "google_secret_manager_secret" "bot_token" {
  secret_id = "${local.app}-bot-token"

  replication {
    auto {}
  }

  depends_on = [
    google_project_service.services
  ]
}

resource "google_secret_manager_secret_version" "bot_token" {
  secret      = google_secret_manager_secret.bot_token.id
  secret_data = var.bot_token

  depends_on = [
    google_secret_manager_secret.bot_token
  ]
}

# ============================================================
# SECRET MANAGER - DATABASE PASSWORD
# ============================================================

resource "google_secret_manager_secret" "db_password" {
  secret_id = "${local.app}-db-password"

  replication {
    auto {}
  }

  depends_on = [
    google_project_service.services
  ]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = local.db_password

  depends_on = [
    google_secret_manager_secret.db_password
  ]
}

# ============================================================
# SERVICE ACCOUNTS
# ============================================================

resource "google_service_account" "main" {
  account_id   = "${local.app}-main"
  display_name = "yt2mf main server"
}

resource "google_service_account" "worker" {
  account_id   = "${local.app}-worker"
  display_name = "yt2mf worker"
}

# ============================================================
# MAIN SERVER IAM
# ============================================================

resource "google_project_iam_member" "main_pubsub" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.main.email}"
}

resource "google_project_iam_member" "main_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.main.email}"
}

resource "google_project_iam_member" "main_secret" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.main.email}"
}

resource "google_storage_bucket_iam_member" "main_bucket" {
  bucket = google_storage_bucket.assets.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.main.email}"
}

# ============================================================
# WORKER IAM
# ============================================================

resource "google_project_iam_member" "worker_subscriber" {
  project = var.project_id
  role    = "roles/pubsub.subscriber"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "worker_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "worker_secret" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_storage_bucket_iam_member" "worker_bucket" {
  bucket = google_storage_bucket.assets.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker.email}"
}

# ============================================================
# MAIN SERVER
# ============================================================

resource "google_compute_instance" "main" {
  name         = "${local.app}-main"
  machine_type = var.machine_type_main
  zone         = var.zone
  tags         = ["yt2mf-main"]
  labels       = local.common_labels

  boot_disk {
    initialize_params {
      image = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64"
      size  = 30
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = google_compute_network.main.name

    access_config {}
  }

  service_account {
    email = google_service_account.main.email

    scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }

  metadata_startup_script = templatefile(
    "${path.module}/startup-main.sh",
    {
      repo_url = var.repo_url

      app    = local.app
      region = var.region

      db_name = var.db_name
      db_user = var.db_user

      db_secret = google_secret_manager_secret.db_password.secret_id

      bot_secret = google_secret_manager_secret.bot_token.secret_id

      bucket = google_storage_bucket.assets.name

      sql_instance = google_sql_database_instance.postgres.connection_name
    }
  )

  depends_on = [
    google_sql_user.app,
    google_secret_manager_secret_version.bot_token,
    google_secret_manager_secret_version.db_password
  ]
}

# ============================================================
# WORKER INSTANCE TEMPLATE
# ============================================================

resource "google_compute_instance_template" "worker" {
  lifecycle {
    create_before_destroy = true
  }
  name_prefix  = "${local.app}-worker-"
  machine_type = var.machine_type_worker

  tags = [
    "yt2mf-worker"
  ]

  labels = local.common_labels

  disk {
    source_image = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64"

    auto_delete = true
    boot        = true

    disk_size_gb = 50
    disk_type    = "pd-balanced"
  }

  network_interface {
    network = google_compute_network.main.name

    access_config {}
  }

  service_account {
    email = google_service_account.worker.email

    scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }

  metadata_startup_script = templatefile(
    "${path.module}/startup-worker.sh",
    {
      repo_url = var.repo_url

      app = local.app

      db_name = var.db_name
      db_user = var.db_user

      db_secret = google_secret_manager_secret.db_password.secret_id

      bot_secret = google_secret_manager_secret.bot_token.secret_id

      bucket = google_storage_bucket.assets.name

      sql_instance = google_sql_database_instance.postgres.connection_name
    }
  )

  depends_on = [
    google_sql_user.app,
    google_secret_manager_secret_version.bot_token,
    google_secret_manager_secret_version.db_password
  ]
}

# ============================================================
# WORKER HEALTH CHECK
# ============================================================

resource "google_compute_health_check" "worker" {
  name = "${local.app}-worker-health"

  check_interval_sec  = 30
  timeout_sec         = 10
  healthy_threshold   = 2
  unhealthy_threshold = 3

  tcp_health_check {
    port = 22
  }
}

# ============================================================
# WORKER MANAGED INSTANCE GROUP
# ============================================================

resource "google_compute_region_instance_group_manager" "workers" {
  name               = "${local.app}-workers"
  region             = var.region
  base_instance_name = "${local.app}-worker"

  version {
    instance_template = google_compute_instance_template.worker.id
  }

  target_size = var.worker_min

  named_port {
    name = "none"
    port = 1
  }

  auto_healing_policies {
    health_check      = google_compute_health_check.worker.id
    initial_delay_sec = 300
  }
}

# ============================================================
# WORKER AUTOSCALER
# ============================================================

resource "google_compute_region_autoscaler" "workers" {
  name   = "${local.app}-worker-autoscaler"
  region = var.region

  target = google_compute_region_instance_group_manager.workers.id

  autoscaling_policy {
    min_replicas    = var.worker_min
    max_replicas    = var.worker_max
    cooldown_period = 300

    metric {
      name = "pubsub.googleapis.com/subscription/num_undelivered_messages"

      filter = "resource.type = pubsub_subscription AND resource.label.subscription_id = ${var.pubsub_subscription}"

      single_instance_assignment = 1
    }
  }
}

# ============================================================
# OUTPUTS
# ============================================================

output "main_ip" {
  value = google_compute_instance.main.network_interface[0].access_config[0].nat_ip
}

output "worker_mig" {
  value = google_compute_region_instance_group_manager.workers.name
}

output "assets_bucket" {
  value = google_storage_bucket.assets.name
}

output "pubsub_topic" {
  value = google_pubsub_topic.jobs.name
}

output "pubsub_subscription" {
  value = google_pubsub_subscription.workers.name
}

output "cloud_sql_instance" {
  value = google_sql_database_instance.postgres.name
}

output "cloud_sql_connection_name" {
  value = google_sql_database_instance.postgres.connection_name
}
