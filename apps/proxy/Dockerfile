FROM nginx:1.27-alpine
RUN apk add --no-cache curl gettext
COPY apps/proxy/nginx.conf.template /etc/nginx/template/nginx.conf.template
COPY apps/proxy/conf.d/ /etc/nginx/template/conf.d/
COPY apps/proxy/docker-entrypoint.sh /usr/local/bin/proxy-entrypoint.sh
RUN chmod +x /usr/local/bin/proxy-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/proxy-entrypoint.sh"]
EXPOSE 80 443
CMD ["nginx", "-g", "daemon off;"]
