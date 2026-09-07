#!/bin/bash
set -e
source /opt/ros/jazzy/setup.bash
source /opt/px4_ws/install/setup.bash
exec "$@"
