# Robot-class Actor image: the SAME CognitiveOS application
# (docker/Dockerfile.base) plus a real ROS 2 + PX4 stack (rclpy,
# px4_msgs, Micro XRCE-DDS Agent) baked into one image, so a drone/robot
# actor's own container can build the REAL Px4RosExecutionAdapter
# (kernel/edge/px4_ros_adapter.py) instead of failing at boot with
# "ModuleNotFoundError: No module named 'rclpy'" -- confirmed live this
# was the actual gap: monkeybrain/agentos:latest (plain python:3.12-slim)
# has the CognitiveOS app but no ROS 2 at all, while the session's
# earlier ad-hoc cognitiveos-px4-ros2:latest had ROS 2 + px4_msgs but
# only the source tree bind-mounted in, never baked into the image
# itself -- neither one alone can be a real drone actor's own container.
#
# Built FROM ros:jazzy-ros-base (Ubuntu 24.04, Python 3.12 -- matches
# python:3.12-slim's version, so requirements.txt installs identically)
# rather than adding ROS onto the slim Debian base, since the ROS 2
# apt packages and their ROS-specific build (px4_msgs via colcon) are
# the harder, slower half to get right; layering the already-proven
# CognitiveOS install (uv pip install -r requirements.txt, then COPY
# the source tree) on top of it is comparatively simple.
FROM ros:jazzy-ros-base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENTOS_AUTH_REQUIRED=false \
    PYTHONPATH=/app:/app/domains/manufacturing/knowledge:/app/sdk/python:/app/packages

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git cmake build-essential python3-pip \
        python3-colcon-common-extensions python3-rosdep \
        libasio-dev libtinyxml2-dev \
    && rm -rf /var/lib/apt/lists/*

# kubectl isn't in Ubuntu noble's default apt repos (unlike the slim
# Debian base docker/Dockerfile.base uses, where plain `apt-get install
# kubectl` genuinely works) -- direct binary download is the standard,
# distro-independent install path (docs/KUBERNETES_PROVISIONER_ENABLEMENT.md).
RUN ARCH=$(dpkg --print-architecture) \
    && curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/${ARCH}/kubectl" \
    && chmod +x /usr/local/bin/kubectl

# Micro XRCE-DDS Agent, built from source (official eProsima mechanism,
# same as this session's earlier /tmp/px4-ros2-build/Dockerfile).
RUN git clone -b v2.4.3 --depth 1 https://github.com/eProsima/Micro-XRCE-DDS-Agent.git /opt/Micro-XRCE-DDS-Agent \
    && cd /opt/Micro-XRCE-DDS-Agent && mkdir build && cd build \
    && cmake .. -DCMAKE_BUILD_TYPE=Release && make -j$(nproc) && make install && ldconfig

# px4_msgs, built as a ROS 2 package in its own workspace (official PX4
# mechanism) -- kernel/edge/px4_ros_adapter.py imports from this.
RUN mkdir -p /opt/px4_ws/src && cd /opt/px4_ws/src \
    && git clone --depth 1 https://github.com/PX4/px4_msgs.git
RUN . /opt/ros/jazzy/setup.sh && cd /opt/px4_ws && colcon build --packages-select px4_msgs

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY requirements.txt .
RUN uv pip install --system --break-system-packages -r requirements.txt

COPY src/ ./src/
COPY services/ ./services/
COPY domains/ ./domains/
COPY packages/ ./packages/
COPY sdk/ ./sdk/
COPY monkeypatched_sdk/ ./monkeypatched_sdk/
COPY deploy/k8s/ ./deploy/k8s/

EXPOSE 8051

# No default CMD -- this image serves three different roles in
# deploy/k8s/drone-actor-deployment.yaml (cognitiveos-actor, xrce-agent,
# mavlink-heartbeat), each overriding `command:` for its own role. The
# ROS 2 + px4_ws overlays must be sourced before any of those Python
# processes import rclpy/px4_msgs -- ros_entrypoint.sh (ros:jazzy-ros-base's
# own base image) already sources /opt/ros/jazzy/setup.sh; this adds the
# px4_msgs overlay on top of it, same pattern as ros_entrypoint.sh itself.
COPY docker/robot-entrypoint.sh /ros_entrypoint_robot.sh
RUN chmod +x /ros_entrypoint_robot.sh
ENTRYPOINT ["/ros_entrypoint_robot.sh"]
CMD ["python3", "-m", "uvicorn", "src.monkey_brain.actor_runtime:app", "--host", "0.0.0.0", "--port", "8051"]
