FROM avpnusr/torprivoxy:latest

USER root

RUN if command -v apk >/dev/null 2>&1; then \
      apk update && apk add --no-cache python3 && apk upgrade --no-cache tor; \
    else \
      apt-get update && apt-get install -y --no-install-recommends python3 \
      && rm -rf /var/lib/apt/lists/*; \
    fi

COPY entrypoint.sh /docker-entrypoint.sh
COPY app /opt/panel
COPY reload.sh /opt/panel/reload.sh

RUN mkdir -p /etc/service/panel \
 && printf '#!/bin/sh\nexec python3 -u /opt/panel/panel.py\n' > /etc/service/panel/run \
 && chmod 755 /docker-entrypoint.sh /opt/panel/reload.sh /etc/service/panel/run \
 && chmod 644 /opt/panel/panel.py /opt/panel/index.html \
 && chown -R tordocker:tordocker /etc/service/panel /opt/panel

USER tordocker

EXPOSE 8099
