param(
    [Parameter(Mandatory = $true)]
    [string]$Text,
    [string]$Source = "manual",
    [string]$SessionId,
    [string]$ExternalId,
    [string]$Url = "",
    [string]$Token
)

if (-not $Url.Trim()) {
    throw "Tracker URL is required."
}

$payload = @{
    text = $Text
    source = $Source
    session_id = $SessionId
    external_id = $ExternalId
    metadata = @{
        captured_from = "send_prompt.ps1"
    }
} | ConvertTo-Json -Depth 4

$headers = @{
    "Content-Type" = "application/json"
}

if ($Token) {
    $headers["X-API-Token"] = $Token
}

Invoke-RestMethod -Method Post -Uri "$($Url.TrimEnd('/'))/api/prompts" -Headers $headers -Body $payload
