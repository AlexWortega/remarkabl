FROM alpine:3.23 AS source
RUN apk add --no-cache git
ARG CLOUD_COMMIT=1958bff18a530038d3916cba0168ad99a24220b4
RUN git clone https://github.com/ddvk/rmfakecloud /src && cd /src && git checkout "$CLOUD_COMMIT"
COPY metadata-size.patch /tmp/metadata-size.patch
RUN cd /src && git apply /tmp/metadata-size.patch
COPY metadata_size_test.go /src/internal/storage/models/metadata_size_test.go

FROM node:24-bookworm-slim AS ui
WORKDIR /src
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
COPY --from=source /src/ui ./
RUN pnpm install --frozen-lockfile && pnpm build

FROM golang:1.24.1-bookworm AS build
WORKDIR /src
COPY --from=source /src ./
COPY --from=ui /src/dist ./ui/dist
ENV GOMAXPROCS=2
RUN go test -p 2 ./internal/storage/models && CGO_ENABLED=0 go build -p 2 -ldflags "-s -w -X main.version=0.0.31-remarkabl.1" -o /rmfakecloud ./cmd/rmfakecloud

FROM alpine:3.23
RUN apk add --no-cache ca-certificates
COPY --from=build /rmfakecloud /usr/local/bin/rmfakecloud
COPY start.sh /usr/local/bin/start-cloud
RUN chmod +x /usr/local/bin/start-cloud
ENV DATADIR=/data OPEN_REGISTRATION=false RM_HTTPS_COOKIE=true RM_TRUST_PROXY=true
EXPOSE 3000
CMD ["/usr/local/bin/start-cloud"]
