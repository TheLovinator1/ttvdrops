# Directory containing your JSON files
$dir = "C:\Responses"

function Matches-SetSessionStatus($content) {
    $topKeys = $content.PSObject.Properties.Name
    if ($topKeys.Count -ne 2 -or -not ($topKeys -contains "data") -or -not ($topKeys -contains "extensions")) { return $false }

    $dataKeys = $content.data.PSObject.Properties.Name
    if ($dataKeys.Count -ne 1 -or $dataKeys -ne "setSessionStatus") { return $false }

    $ssKeys = $content.data.setSessionStatus.PSObject.Properties.Name
    if ($ssKeys.Count -ne 2 -or -not ($ssKeys -contains "setAgainInSeconds") -or -not ($ssKeys -contains "__typename")) { return $false }

    if ($content.data.setSessionStatus.__typename -ne "SetSessionStatusPayload") { return $false }

    $extKeys = $content.extensions.PSObject.Properties.Name
    if ($extKeys.Count -ne 3 -or -not ($extKeys -contains "durationMilliseconds") -or -not ($extKeys -contains "operationName") -or -not ($extKeys -contains "requestID")) { return $false }

    return ($content.extensions.operationName -eq "ChannelPage_SetSessionStatus")
}

function Matches-UseLive($content) {
    $topKeys = $content.PSObject.Properties.Name
    if ($topKeys.Count -ne 2 -or -not ($topKeys -contains "data") -or -not ($topKeys -contains "extensions")) { return $false }

    $dataKeys = $content.data.PSObject.Properties.Name
    if ($dataKeys.Count -ne 1 -or $dataKeys -ne "user") { return $false }

    $userKeys = $content.data.user.PSObject.Properties.Name
    if ($userKeys.Count -ne 4 -or -not ($userKeys -contains "id") -or -not ($userKeys -contains "login") -or -not ($userKeys -contains "stream") -or -not ($userKeys -contains "__typename")) { return $false }

    if ($content.data.user.__typename -ne "User") { return $false }

    $extKeys = $content.extensions.PSObject.Properties.Name
    if ($extKeys.Count -ne 3 -or -not ($extKeys -contains "durationMilliseconds") -or -not ($extKeys -contains "operationName") -or -not ($extKeys -contains "requestID")) { return $false }

    return ($content.extensions.operationName -eq "UseLive")
}

function Matches-StoryPreviewsWithOrder($content) {
    $topKeys = $content.PSObject.Properties.Name
    if ($topKeys.Count -ne 2 -or -not ($topKeys -contains "data") -or -not ($topKeys -contains "extensions")) { return $false }

    $dataKeys = $content.data.PSObject.Properties.Name
    if ($dataKeys.Count -ne 1 -or $dataKeys -ne "storyPreviewsWithOrder") { return $false }

    $spKeys = $content.data.storyPreviewsWithOrder.PSObject.Properties.Name
    if ($spKeys.Count -ne 4 -or -not ($spKeys -contains "previews") -or -not ($spKeys -contains "hasNextPage") -or -not ($spKeys -contains "isFresh") -or -not ($spKeys -contains "__typename")) { return $false }

    if ($content.data.storyPreviewsWithOrder.__typename -ne "StoryPreviewsWithOrder") { return $false }

    $extKeys = $content.extensions.PSObject.Properties.Name
    if ($extKeys.Count -ne 3 -or -not ($extKeys -contains "durationMilliseconds") -or -not ($extKeys -contains "operationName") -or -not ($extKeys -contains "requestID")) { return $false }

    return ($content.extensions.operationName -eq "StoryPreviewsWithOrder")
}

function Matches-ConnectAdIdentity($content) {
    $topKeys = $content.PSObject.Properties.Name
    if ($topKeys.Count -ne 2 -or -not ($topKeys -contains "data") -or -not ($topKeys -contains "extensions")) { return $false }

    $dataKeys = $content.data.PSObject.Properties.Name
    if ($dataKeys.Count -ne 1 -or $dataKeys -ne "connectAdIdentity") { return $false }

    $adKeys = $content.data.connectAdIdentity.PSObject.Properties.Name
    if ($adKeys.Count -ne 2 -or -not ($adKeys -contains "identityURL") -or -not ($adKeys -contains "__typename")) { return $false }

    if ($content.data.connectAdIdentity.__typename -ne "ConnectAdIdentityPayload") { return $false }

    $extKeys = $content.extensions.PSObject.Properties.Name
    if ($extKeys.Count -ne 3 -or -not ($extKeys -contains "durationMilliseconds") -or -not ($extKeys -contains "operationName") -or -not ($extKeys -contains "requestID")) { return $false }

    return ($content.extensions.operationName -eq "ConnectAdIdentityMutation")
}

function Matches-CurrentUser($content) {
    $topKeys = $content.PSObject.Properties.Name
    if ($topKeys.Count -ne 2 -or -not ($topKeys -contains "data") -or -not ($topKeys -contains "extensions")) { return $false }

    $dataKeys = $content.data.PSObject.Properties.Name
    if ($dataKeys.Count -ne 1 -or $dataKeys -ne "currentUser") { return $false }

    $cuKeys = $content.data.currentUser.PSObject.Properties.Name
    if ($cuKeys.Count -ne 6 -or -not ($cuKeys -contains "id") -or -not ($cuKeys -contains "login") -or -not ($cuKeys -contains "hasTurbo") -or -not ($cuKeys -contains "hasPrime") -or -not ($cuKeys -contains "language") -or -not ($cuKeys -contains "__typename")) { return $false }

    if ($content.data.currentUser.__typename -ne "User") { return $false }

    $extKeys = $content.extensions.PSObject.Properties.Name
    if ($extKeys.Count -ne 3 -or -not ($extKeys -contains "durationMilliseconds") -or -not ($extKeys -contains "operationName") -or -not ($extKeys -contains "requestID")) { return $false }

    return ($content.extensions.operationName -eq "Core_Services_Spade_CurrentUser")
}

function Matches-CoreServicesSpadeCurrentUser {
    param ($json)
    return $json.data.currentUser -and
           $json.extensions.operationName -eq "Core_Services_Spade_CurrentUser"
}

function Matches-TopNavCurrentUser {
    param ($json)
    return $json.data.currentUser -and
           $json.data.requestInfo -and
           $json.extensions.operationName -eq "TopNav_CurrentUser"
}

function Matches-GuestStarBatchCollaboration {
    param($json)

    return (
        $json.PSObject.Properties.Name -contains 'data' -and
        $json.PSObject.Properties.Name -contains 'extensions' -and

        $json.data.PSObject.Properties.Name -contains 'guestStarChannelCollaboration' -and
        $json.data.PSObject.Properties.Name -contains 'guestStarCollaborationStatuses' -and

        $json.extensions.operationName -eq 'GuestStarBatchCollaborationQuery'
    )
}

function Matches-OnsiteNotificationsSummary {
    param($json)

    return (
        $json.PSObject.Properties.Name -contains 'data' -and
        $json.PSObject.Properties.Name -contains 'extensions' -and

        $json.data.PSObject.Properties.Name -contains 'currentUser' -and
        $json.data.currentUser.PSObject.Properties.Name -contains 'id' -and
        $json.data.currentUser.PSObject.Properties.Name -contains 'notifications' -and
        $json.data.currentUser.notifications.PSObject.Properties.Name -contains 'summary' -and
        $json.data.currentUser.notifications.summary.PSObject.Properties.Name -contains 'unseenCount' -and
        $json.data.currentUser.notifications.summary.PSObject.Properties.Name -contains 'viewerUnreadSummary' -and
        $json.data.currentUser.notifications.summary.PSObject.Properties.Name -contains 'creatorUnreadSummary' -and

        $json.extensions.operationName -eq 'OnsiteNotifications_Summary'
    )
}

# Counter
$deleted = 0
$skipped = 0

# Loop through files and check
Get-ChildItem -Path $dir -Filter *.json | ForEach-Object {
    try {
        $content = Get-Content $_.FullName -Raw | ConvertFrom-Json -ErrorAction Stop

        if ((Matches-SetSessionStatus $content) -or 
            (Matches-UseLive $content) -or 
            (Matches-StoryPreviewsWithOrder $content) -or 
            (Matches-ConnectAdIdentity $content) -or
            (Matches-CoreServicesSpadeCurrentUser $content) -or
            (Matches-TopNavCurrentUser $content) -or
            (Matches-GuestStarBatchCollaboration $content) -or
            (Matches-OnsiteNotificationsSummary $content)) {

            Write-Host "Deleting file: $($_.FullName)"
            Remove-Item $_.FullName -Force
            $deleted++
        }
        else {
            $skipped++
        }
    }
    catch {
        Write-Host "Skipping invalid JSON file: $($_.FullName)"
        $skipped++
    }
}

Write-Host "Done. Deleted: $deleted, Skipped: $skipped"
