#!/bin/bash
# Starts a virtual X display, VNC server, and noVNC web proxy, then launches
# Gazebo + Nav2 non-headless against that virtual display -- see
# docker/Dockerfile.ros-bridge-nav2's own comment for why this exists
# (native X11-forward-to-macOS/XQuartz fails on GLX FBConfig negotiation;
# rendering inside the container and shipping only pixels out over VNC
# avoids that).
set -e

DISPLAY_NUM="${VNC_DISPLAY_NUM:-99}"
export DISPLAY=":${DISPLAY_NUM}"

# Xtigervnc (TigerVNC's own X server), NOT Xvfb + a separate VNC capture
# tool -- Xtigervnc IS the X display AND natively serves VNC on rfbport,
# in one process, so there is nothing separate to "attach" and nothing
# that can silently fail to attach. Confirmed live this base image's
# tigervnc-standalone-server package doesn't even ship x0vncserver (the
# capture-an-existing-display tool other TigerVNC packagings provide) --
# only Xtigervnc/tigervncserver/tigervncsession -- and, separately, that
# x11vnc 0.9.16 is unstable against an Xvfb + actively-rendering Ogre2/GL
# setup (process died into a zombie; a fresh instance never sent the RFB
# greeting despite reporting itself as listening). One process for both
# roles sidesteps both problems at once.
# -SecurityTypes None: no VNC password -- this port is only ever reached
# via `kubectl port-forward` to the user's own machine, never exposed as
# a NodePort/LoadBalancer; add real security before changing that.
Xtigervnc "$DISPLAY" -geometry 1280x800 -depth 24 -SecurityTypes None -rfbport 5900 &

# Give Xtigervnc a moment to actually create the display socket before
# anything tries to render to it.
for _ in $(seq 1 20); do
  if [ -e "/tmp/.X11-unix/X${DISPLAY_NUM}" ]; then
    break
  fi
  sleep 0.5
done

websockify --web=/usr/share/novnc/ 6080 localhost:5900 &

source /opt/ros/jazzy/setup.bash
exec ros2 launch nav2_bringup tb3_simulation_launch.py headless:=False use_rviz:=False
