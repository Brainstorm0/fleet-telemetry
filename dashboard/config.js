// Default settings for running on your laptop with local_demo/server.py.
// (The local server actually generates this file for you, and Terraform
// uploads a cloud version of it to S3 - so you normally never edit this.)
window.FLEET_CONFIG = {
  apiUrl: "http://localhost:8000/api",
  wsUrl: "ws://localhost:8000/ws",
  mode: "local"
};
