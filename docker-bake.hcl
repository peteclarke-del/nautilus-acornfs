group "default" {
  targets = ["test"]
}

target "test" {
  context = "."
  dockerfile = "containers/dev.Dockerfile"
  platforms = ["linux/amd64", "linux/arm64", "linux/arm/v7"]
}

