ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONPATH=/app \
    DISPLAY=:98 NO_AT_BRIDGE=0 QT_ACCESSIBILITY=1 GTK_MODULES=gail:atk-bridge
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-aiohttp python3-gi gir1.2-atspi-2.0 gir1.2-gtk-3.0 at-spi2-core \
    dbus-x11 xvfb x11vnc novnc websockify ca-certificates fonts-noto-cjk \
    libnss3 libasound2t64 libgbm1 libxss1 libxcomposite1 libxdamage1 libxrandr2 \
    libxfixes3 libxtst6 libgtk-3-0t64 libdrm2 libnotify4 libsecret-1-0 \
    libayatana-appindicator3-1 curl && rm -rf /var/lib/apt/lists/*
ARG QQ_DEB_URL
ARG QQ_DEB_SHA256
RUN test -n "$QQ_DEB_SHA256" && \
    case "$QQ_DEB_URL" in https://qqdl.gtimg.cn/qqfile/*.deb|https://dldir1.qq.com/qqfile/*.deb) ;; *) exit 1 ;; esac && \
    curl --fail --location --proto '=https' --proto-redir '=https' --max-time 300 -A 'Mozilla/5.0' -e 'https://im.qq.com/' "$QQ_DEB_URL" -o /tmp/qq.deb && \
    echo "$QQ_DEB_SHA256  /tmp/qq.deb" | sha256sum -c - && \
    dpkg-deb -x /tmp/qq.deb /opt/qq-client && rm /tmp/qq.deb && \
    test -x /opt/qq-client/opt/QQ/qq
RUN useradd --create-home --uid 10001 bridge && mkdir /data && chown bridge:bridge /data
COPY qq_desktop /app/qq_desktop
COPY tests /app/tests
COPY scripts /app/scripts
COPY config.example.json /app/config.example.json
RUN chmod +x /app/scripts/*.sh
USER bridge
WORKDIR /app
EXPOSE 3002 6080
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
