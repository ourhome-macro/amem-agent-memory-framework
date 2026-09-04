param(
    [string]$BaseUrl = "http://127.0.0.1:5000",
    [string]$ComposeFile = "docker-compose.go.yml",
    [int]$TimeoutSeconds = 60,
    [switch]$SkipComposeUp
)

$ErrorActionPreference = "Stop"

function Fail($Message) {
    throw "[e2e-mq] $Message"
}

function Assert-True($Condition, $Message) {
    if (-not $Condition) {
        Fail $Message
    }
}

function Invoke-MySql([string]$Sql) {
    Ensure-MySqlEnv
    & docker compose -f $ComposeFile exec -T mysql env "MYSQL_PWD=$script:MySqlPassword" mysql "-u$script:MySqlUser" $script:MySqlDatabase -N -B -e $Sql
    if ($LASTEXITCODE -ne 0) {
        Fail "mysql query failed: $Sql"
    }
}

function Ensure-MySqlEnv {
    if ($script:MySqlUser -and $script:MySqlPassword -and $script:MySqlDatabase) {
        return
    }
    $script:MySqlUser = ((& docker compose -f $ComposeFile exec -T mysql printenv MYSQL_USER) | Select-Object -First 1).Trim()
    $script:MySqlPassword = ((& docker compose -f $ComposeFile exec -T mysql printenv MYSQL_PASSWORD) | Select-Object -First 1).Trim()
    $script:MySqlDatabase = ((& docker compose -f $ComposeFile exec -T mysql printenv MYSQL_DATABASE) | Select-Object -First 1).Trim()
    Assert-True ($script:MySqlUser -and $script:MySqlPassword -and $script:MySqlDatabase) "could not read MySQL env from compose service"
}

function Invoke-MySqlScalar([string]$Sql) {
    $value = Invoke-MySql $Sql
    if ($null -eq $value) {
        return ""
    }
    return (($value | Select-Object -First 1) -as [string]).Trim()
}

function Invoke-ApiJson([string]$Method, [string]$Path, $Body = $null) {
    $uri = "$BaseUrl$Path"
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $uri -TimeoutSec 15
    }
    $json = $Body | ConvertTo-Json -Depth 20 -Compress
    return Invoke-RestMethod -Method $Method -Uri $uri -ContentType "application/json" -Body $json -TimeoutSec 15
}

function Wait-Until([scriptblock]$Probe, [string]$Description) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $last = $null
    do {
        try {
            $last = & $Probe
            if ($last) {
                return
            }
        } catch {
            $last = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 750
    } while ((Get-Date) -lt $deadline)
    Fail "timed out waiting for $Description; last=$last"
}

function Wait-Ready {
    Wait-Until {
        $response = Invoke-ApiJson "GET" "/health/ready"
        return ($response.success -eq $true -and $response.data.status -eq "ready")
    } "backend readiness"
}

function Wait-MqEvent([string]$EventId) {
    Wait-Until {
        $status = Invoke-MySqlScalar "SELECT status FROM outbox_events WHERE event_id = '$EventId'"
        $consumerCount = [int](Invoke-MySqlScalar "SELECT COUNT(*) FROM consumer_idempotency WHERE event_id = '$EventId'")
        return ($status -eq "published" -and $consumerCount -eq 3)
    } "RocketMQ consumers for $EventId"
}

function Metric-Count([string]$EventType, [string]$Scene, [string]$Source) {
    $value = Invoke-MySqlScalar "SELECT COALESCE(SUM(count), 0) FROM behavior_metric_counters WHERE event_type = '$EventType' AND scene = '$Scene' AND source = '$Source'"
    if ($value -eq "") {
        return 0
    }
    return [int64]$value
}

function Assert-EventCore([string]$EventId, [string]$ExpectedType) {
    $behaviorCount = [int](Invoke-MySqlScalar "SELECT COUNT(*) FROM behavior_events WHERE event_id = '$EventId' AND event_type = '$ExpectedType'")
    $outboxCount = [int](Invoke-MySqlScalar "SELECT COUNT(*) FROM outbox_events WHERE event_id = '$EventId' AND status = 'published'")
    $consumerCount = [int](Invoke-MySqlScalar "SELECT COUNT(*) FROM consumer_idempotency WHERE event_id = '$EventId'")
    Assert-True ($behaviorCount -eq 1) "expected one behavior_events row for $EventId/$ExpectedType, got $behaviorCount"
    Assert-True ($outboxCount -eq 1) "expected one published outbox row for $EventId, got $outboxCount"
    Assert-True ($consumerCount -eq 3) "expected three consumer idempotency rows for $EventId, got $consumerCount"
}

if (-not $SkipComposeUp) {
    Write-Host "[e2e-mq] starting compose stack"
    & docker compose -f $ComposeFile up -d --build amem backend-go
    if ($LASTEXITCODE -ne 0) {
        Fail "docker compose up failed"
    }
}

Write-Host "[e2e-mq] waiting for backend readiness"
Wait-Ready

$migrationCount = [int](Invoke-MySqlScalar "SELECT COUNT(*) FROM schema_migrations WHERE version = '0002_mq_consumers.sql'")
Assert-True ($migrationCount -eq 1) "0002_mq_consumers.sql has not been applied"

$suffix = "e2e-$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())-$PID"
$source = "e2e"
$dlqBefore = [int](Invoke-MySqlScalar "SELECT COUNT(*) FROM consumer_dlq_events")

Write-Host "[e2e-mq] testing playback behavior outbox and idempotency"
$playEventId = "play-$suffix"
$playBefore = Metric-Count "play" "playback" $source
$playBody = @{
    eventId = $playEventId
    event = "play"
    trackId = "BV1E2EPLAY:$PID"
    scene = "playback"
    listenMs = 42000
    source = $source
}
$playResponse = Invoke-ApiJson "POST" "/api/playback/events" $playBody
Assert-True ($playResponse.success -eq $true -and $playResponse.data.accepted -eq $true) "playback event was not accepted"
Wait-MqEvent $playEventId
Assert-EventCore $playEventId "play"

$duplicateResponse = Invoke-ApiJson "POST" "/api/playback/events" $playBody
Assert-True ($duplicateResponse.success -eq $true -and $duplicateResponse.data.accepted -eq $true) "duplicate playback event was not accepted"
Start-Sleep -Seconds 2
$playAfter = Metric-Count "play" "playback" $source
Assert-True ($playAfter -eq ($playBefore + 1)) "play metric should increment once despite duplicate event_id; before=$playBefore after=$playAfter"

Write-Host "[e2e-mq] testing recommendation click feedback"
$clickEventId = "click-$suffix"
$trackID = "BV1E2ECLICK:$PID"
$bvid = "BV1E2ECLICK"
$clickBefore = Metric-Count "recommendation.clicked" "home" $source
Invoke-MySql "INSERT INTO tracks (track_id, bvid, cid, title, owner, owner_mid, cover, duration, play_count, published_at, page, page_title, source, raw_json, updated_at) VALUES ('$trackID', '$bvid', $PID, 'E2E MQ Track', 'E2E', $PID, '', 180, 0, '', 1, 'E2E MQ Track', 'bili', JSON_OBJECT(), NOW(3)) ON DUPLICATE KEY UPDATE updated_at = NOW(3)"
Invoke-MySql "INSERT INTO recommendation_history (user_id, track_id, recommended_at, scene, source, score, reason) VALUES ('legacy-owner', '$trackID', NOW(3), 'home', '$source', 0.91, 'e2e mq click')"
$clickResponse = Invoke-ApiJson "POST" "/api/recommendations/events" @{
    eventId = $clickEventId
    event = "clicked"
    trackId = $trackID
    scene = "home"
    source = $source
    score = 0.91
    reason = "e2e mq click"
}
Assert-True ($clickResponse.success -eq $true -and $clickResponse.data.accepted -eq $true) "recommendation click was not accepted"
Wait-MqEvent $clickEventId
Assert-EventCore $clickEventId "recommendation.clicked"
$clickedRows = [int](Invoke-MySqlScalar "SELECT COUNT(*) FROM recommendation_history WHERE user_id = 'legacy-owner' AND track_id = '$trackID' AND clicked = 1")
Assert-True ($clickedRows -ge 1) "recommendation_history was not marked clicked for $trackID"
$clickAfter = Metric-Count "recommendation.clicked" "home" $source
Assert-True ($clickAfter -eq ($clickBefore + 1)) "click metric mismatch; before=$clickBefore after=$clickAfter"

Write-Host "[e2e-mq] testing recommendation exposure itemCount metric"
$exposedEventId = "exposed-$suffix"
$exposedBefore = Metric-Count "recommendation.exposed" "home" $source
$exposedResponse = Invoke-ApiJson "POST" "/api/recommendations/events" @{
    eventId = $exposedEventId
    event = "exposed"
    scene = "home"
    source = $source
    itemCount = 3
    items = @(
        @{ trackId = "$trackID-a" },
        @{ trackId = "$trackID-b" },
        @{ trackId = "$trackID-c" }
    )
}
Assert-True ($exposedResponse.success -eq $true -and $exposedResponse.data.accepted -eq $true) "recommendation exposure was not accepted"
Wait-MqEvent $exposedEventId
Assert-EventCore $exposedEventId "recommendation.exposed"
$exposedAfter = Metric-Count "recommendation.exposed" "home" $source
Assert-True ($exposedAfter -eq ($exposedBefore + 3)) "exposure metric should increment by itemCount=3; before=$exposedBefore after=$exposedAfter"

$profile = Invoke-ApiJson "GET" "/api/profile/music"
Assert-True ($profile.success -eq $true) "music profile endpoint failed"

$dlqAfter = [int](Invoke-MySqlScalar "SELECT COUNT(*) FROM consumer_dlq_events")
Assert-True ($dlqAfter -eq $dlqBefore) "DLQ count changed; before=$dlqBefore after=$dlqAfter"

Write-Host "[e2e-mq] PASS"
Write-Host "[e2e-mq] event_ids: $playEventId, $clickEventId, $exposedEventId"
