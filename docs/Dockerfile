# Knowledge base of KRAKEN-Trust: Markdown (content/) built into a static site
# by MkDocs Material, then served by an unprivileged nginx (reached through the
# console at http://localhost:8080/docs/).
FROM python:3.12-slim AS build
WORKDIR /src
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mkdocs.yml .
COPY content ./content
RUN mkdocs build --strict --site-dir /site

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /site /usr/share/nginx/html/docs
EXPOSE 8080
