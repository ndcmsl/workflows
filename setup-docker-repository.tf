terraform {
  required_providers {
    dockerhub = {
      source = "BarnabyShearer/dockerhub"
      version = "0.0.8"
    }
  }
}

provider "dockerhub" {
  username = var.docker_user
  password = var.docker_pass
}

resource "dockerhub_repository" "repository" {
  name = var.repo_name
  namespace = "ndcmsl"
  private = true
}

variable "docker_user" {
  type = string
}

variable "docker_pass" {
  type = string
}

variable "repo_name" {
  type = string
}