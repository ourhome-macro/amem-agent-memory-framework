import { useRouter } from 'vue-router'
import type { Track } from '@/types'

type TrackWithLegacyOwner = Track & {
  owner_mid?: unknown
  mid?: unknown
  owner?: string
  upper?: { mid?: unknown } | null
  ownerInfo?: { mid?: unknown } | null
}

function normalizedMid(value: unknown): number | null {
  const mid = Number(value)
  return Number.isFinite(mid) && mid > 0 ? mid : null
}

function ownerMidFromTrack(track: Track | null | undefined): number | null {
  if (!track) return null
  const raw = track as TrackWithLegacyOwner
  return normalizedMid(raw.ownerMid)
    ?? normalizedMid(raw.owner_mid)
    ?? normalizedMid(raw.mid)
    ?? normalizedMid(raw.upper?.mid)
    ?? normalizedMid(raw.ownerInfo?.mid)
}

export function useOpenOwner() {
  const router = useRouter()

  async function openTrackOwner(track: Track | null | undefined): Promise<boolean> {
    if (!track?.bvid) return false
    const directMid = ownerMidFromTrack(track)
    if (directMid) {
      await router.push(`/up/${directMid}`)
      return true
    }

    const params = new URLSearchParams()
    if (track.cid != null) params.set('cid', String(track.cid))
    if (track.owner) params.set('owner', track.owner)
    const query = params.toString()
    await router.push(`/up/resolve/${encodeURIComponent(track.bvid)}${query ? `?${query}` : ''}`)
    return true
  }

  return { openTrackOwner }
}
