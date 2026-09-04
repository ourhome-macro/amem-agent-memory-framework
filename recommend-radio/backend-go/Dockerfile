FROM golang:1.25-alpine AS build

WORKDIR /src

RUN apk add --no-cache git ca-certificates

COPY backend-go/go.mod backend-go/go.sum ./
RUN go mod download

COPY backend-go ./

ARG GO_BUILD_TAGS=""
RUN if [ -n "$GO_BUILD_TAGS" ]; then \
      go test -tags "$GO_BUILD_TAGS" ./... && go build -tags "$GO_BUILD_TAGS" -o /out/recommend-radio-go ./cmd/server; \
    else \
      go test ./... && go build -o /out/recommend-radio-go ./cmd/server; \
    fi

FROM alpine:3.22

RUN apk add --no-cache ca-certificates wget tzdata

WORKDIR /app

COPY --from=build /out/recommend-radio-go /app/recommend-radio-go

EXPOSE 5000

HEALTHCHECK --interval=15s --timeout=5s --retries=10 --start-period=20s \
  CMD wget -qO- http://127.0.0.1:5000/health/ready >/dev/null || exit 1

ENTRYPOINT ["/app/recommend-radio-go"]
