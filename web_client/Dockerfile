# customer网页客户端的静态站点镜像。
# 与zhitian_admin保持同一套容器化模式：nginx:stable-alpine、移除默认站点、
# 临时目录改到/tmp并归属nginx用户、以非root的nginx身份运行、监听8080。
# 镜像本身不感知对外路径前缀：反向代理负责把/customer/剥掉后转发，因此
# Phase B改为子域名分流时无需重建镜像。
FROM nginx:stable-alpine

RUN rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p \
        /tmp/nginx/client_temp \
        /tmp/nginx/proxy_temp \
        /tmp/nginx/fastcgi_temp \
        /tmp/nginx/uwsgi_temp \
        /tmp/nginx/scgi_temp \
    && chown -R nginx:nginx /tmp/nginx /usr/share/nginx/html

COPY nginx.conf /etc/nginx/nginx.conf
COPY --chown=nginx:nginx css/ /usr/share/nginx/html/css/
COPY --chown=nginx:nginx js/ /usr/share/nginx/html/js/
COPY --chown=nginx:nginx config.js /usr/share/nginx/html/config.js
COPY --chown=nginx:nginx *.html /usr/share/nginx/html/

USER nginx

EXPOSE 8080

ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
