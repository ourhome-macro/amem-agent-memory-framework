FROM golang:1.25-alpine AS build

WORKDIR /src
ARG GOPROXY=https://goproxy.cn,direct
ENV GOPROXY=${GOPROXY}
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/sse-gateway .

FROM alpine:3.22
RUN addgroup -S app && adduser -S -G app app
WORKDIR /app
COPY --from=build /out/sse-gateway /app/sse-gateway
RUN mkdir -p /app/data && chown -R app:app /app
USER app

ENV SSE_BIND=0.0.0.0:8080
ENV SSE_DB_PATH=/app/data/sse-events.sqlite3
EXPOSE 8080

ENTRYPOINT ["/app/sse-gateway"]
