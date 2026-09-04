<template>
  <div class="page home">
    <header class="welcome">
      <div>
        <h1>{{ companionGreeting }}</h1>
        <p class="sub">告诉我今天的状态，我会把推荐调到更合拍的节奏。</p>
      </div>
      <RouterLink to="/agent" class="agent-link">
        <AppIcon name="message" :size="16" />
        <span>和音乐搭子聊聊</span>
      </RouterLink>
    </header>

    <section v-if="recent.length" class="section">
      <SectionHeader title="最近播放">
        <template #extra>
          <RouterLink to="/recent" class="more-link">查看全部</RouterLink>
        </template>
      </SectionHeader>
      <div class="card-grid">
        <TrackCard
          v-for="track in recent.slice(0, 5)"
          :key="track.trackId ?? track.bvid"
          :track="track"
          removable
          @play="player.playTrack(track)"
          @remove="removeRecent(track)"
        />
      </div>
    </section>

    <section class="section">
      <SectionHeader title="为你推荐" :count="recommendations.length" />
      <div v-if="recommendationLoading" class="pending-text">正在计算推荐...</div>
      <div v-else-if="recommendations.length" class="recommend-list">
        <article
          v-for="item in recommendations"
          :key="item.track.trackId ?? `${item.track.bvid}:${item.track.cid ?? item.source}`"
          class="recommend-row"
        >
          <button class="recommend-main" @click="playRecommendation(item)">
            <img class="recommend-cover" :src="mediaUrl(item.track.cover)" :alt="item.track.title" loading="lazy" />
            <span class="recommend-copy">
              <strong :title="item.track.title">{{ item.track.title }}</strong>
              <small>{{ item.reason }}</small>
            </span>
          </button>
          <button class="recommend-dismiss" title="不感兴趣" @click="dismissRecommendation(item)">
            <AppIcon name="close" :size="16" />
          </button>
        </article>
      </div>
      <p v-else class="pending-text">先播放、喜欢或评价几首歌，推荐会自动出现。</p>
    </section>

    <section class="section profile-section">
      <SectionHeader title="今天我会这样陪你听" />
      <div class="profile-grid">
        <div class="profile-panel">
          <div class="profile-meta">
            <span>结合最近播放、收藏、反馈和聊天里的状态</span>
          </div>
          <div class="signal-row">
            <span
              v-for="item in topSignals(musicProfile?.profile.positive_topics)"
              :key="`positive-${item.name}`"
              class="signal-chip positive"
            >
              {{ item.name }}
            </span>
            <span
              v-for="item in topSignals(musicProfile?.profile.negative_topics)"
              :key="`negative-${item.name}`"
              class="signal-chip negative"
            >
              {{ item.name }}
            </span>
            <span
              v-for="item in topSignals(musicProfile?.profile.mood_weights)"
              :key="`mood-${item.name}`"
              class="signal-chip mood"
            >
              {{ item.name }}
            </span>
          </div>
          <form class="statement-form" @submit.prevent="saveProfileStatement">
            <textarea
              v-model="profileStatement"
              rows="4"
              maxlength="2000"
              placeholder="直接说今天想怎么听：面试准备得很累，想安静一点；或者最近突然喜欢夜跑，想要轻律动。"
            />
            <div class="statement-actions">
              <span>{{ statementStatus }}</span>
              <button type="submit" :disabled="statementSaving || !profileStatement.trim()">
                {{ statementSaving ? '理解中' : '告诉音乐搭子' }}
              </button>
            </div>
          </form>
        </div>
        <div class="profile-panel">
          <div class="profile-meta">
            <span>为什么会出现这些歌</span>
          </div>
          <p class="reason-copy">{{ recommendationReasonSummary }}</p>
          <ol v-if="recommendationTrace?.finalResults?.length" class="trace-list">
            <li v-for="item in recommendationTrace.finalResults.slice(0, 5)" :key="item.trackId ?? item.bvid">
              <span>{{ item.title }}</span>
              <small>{{ item.reason || '贴近你最近的听歌状态' }}</small>
            </li>
          </ol>
          <p v-else class="pending-text">先聊几句或听几首，我会把原因说成人能看懂的话。</p>
        </div>
      </div>
    </section>

    <section class="section">
      <SectionHeader title="我的播放次数 Top 10" :count="playCountRanking.length" />
      <div v-if="playCountRanking.length" class="rank-list">
        <article
          v-for="(track, i) in playCountRanking"
          :key="track.trackId ?? `${track.bvid}:${track.cid ?? i}`"
          class="rank-row"
        >
          <button class="rank-main" @click="player.playTrack(track)">
            <span class="rank-index">{{ i + 1 }}</span>
            <img class="rank-cover" :src="mediaUrl(track.cover)" :alt="track.title" loading="lazy" />
            <span class="rank-title" :title="track.title">{{ track.title }}</span>
            <span class="rank-owner">{{ track.owner }}</span>
            <span class="rank-count">已播放 {{ formatCount(track.recentPlayCount ?? 0) }} 次</span>
          </button>
          <button class="rank-remove" title="删除" @click="removeRecent(track)">
            <AppIcon name="close" :size="15" />
          </button>
        </article>
      </div>
      <p v-else class="empty-text">暂无播放次数数据，先搜索或播放几首内容。</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { storeToRefs } from 'pinia'
import {
  fetchLatestRecommendationDebug,
  fetchMusicProfile,
  fetchRecommendations,
  mediaUrl,
  recordRecommendationEvent,
  submitMusicProfileStatement,
} from '@/api/client'
import { usePlayerStore } from '@/stores/playerStore'
import { useLibraryStore } from '@/stores/libraryStore'
import type { MusicProfileAnalysis, RecommendationDebugTrace, RecommendationItem, Track } from '@/types'
import { formatCount } from '@/utils/format'
import TrackCard from '@/components/TrackCard.vue'
import AppIcon from '@/components/base/AppIcon.vue'
import SectionHeader from '@/components/base/SectionHeader.vue'

const player = usePlayerStore()
const library = useLibraryStore()
const { recent } = storeToRefs(library)
const recommendations = ref<RecommendationItem[]>([])
const recommendationLoading = ref(false)
const musicProfile = ref<MusicProfileAnalysis | null>(null)
const recommendationTrace = ref<RecommendationDebugTrace | null>(null)
const profileStatement = ref('')
const statementSaving = ref(false)
const statementStatus = ref('')

onMounted(() => {
  void loadRecommendations()
})

const companionGreeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了，来点低打扰的歌'
  if (hour < 12) return '上午好，先找个舒服节奏'
  if (hour < 14) return '中午好，听点不费劲的'
  if (hour < 18) return '下午好，接着听点合拍的'
  return '晚上好，今天想听什么'
})

const playCountRanking = computed(() => {
  return uniqueTracks(recent.value)
    .filter((track) => Number.isFinite(track.recentPlayCount) && (track.recentPlayCount ?? 0) > 0)
    .sort((a, b) => (b.recentPlayCount ?? 0) - (a.recentPlayCount ?? 0))
    .slice(0, 10)
})

const recommendationReasonSummary = computed(() => {
  const profile = musicProfile.value?.profile
  const positives = topSignals(profile?.positive_topics).map((item) => item.name).slice(0, 3)
  const moods = topSignals(profile?.mood_weights).map((item) => item.name).slice(0, 2)
  const negatives = topSignals(profile?.negative_topics).map((item) => item.name).slice(0, 2)
  const parts: string[] = []
  if (positives.length) parts.push(`先贴近 ${positives.join('、')} 这些长期更合拍的方向。`)
  if (moods.length) parts.push(`听感上会照顾 ${moods.join('、')} 这类状态。`)
  if (negatives.length) parts.push(`同时减少 ${negatives.join('、')}。`)
  return parts.join('') || '我会结合最近播放、收藏和反馈来选，避免只按单一标签硬推。'
})

async function loadRecommendations() {
  recommendationLoading.value = true
  try {
    const result = await fetchRecommendations('home', 8)
    recommendations.value = result.items
    await loadRecommendationInsights()
  } catch {
    recommendations.value = []
  } finally {
    recommendationLoading.value = false
  }
}

function playRecommendation(item: RecommendationItem) {
  player.playTrack(item.track)
  void recordRecommendationEvent({
    trackId: item.track.trackId ?? trackIdentity(item.track),
    event: 'played',
    scene: 'home',
    source: item.source,
    reason: item.reason,
    score: item.score,
  })
}

function dismissRecommendation(item: RecommendationItem) {
  recommendations.value = recommendations.value.filter((candidate) => candidate !== item)
  void recordRecommendationEvent({
    trackId: item.track.trackId ?? trackIdentity(item.track),
    event: 'dismissed',
    scene: 'home',
    source: item.source,
    reason: item.reason,
    score: item.score,
  })
}

async function loadRecommendationInsights() {
  const [profile, trace] = await Promise.allSettled([
    fetchMusicProfile('home'),
    fetchLatestRecommendationDebug('home'),
  ])
  if (profile.status === 'fulfilled') {
    musicProfile.value = profile.value
  }
  if (trace.status === 'fulfilled') {
    recommendationTrace.value = trace.value
  }
}

function topSignals(values: Record<string, number> | undefined): Array<{ name: string; weight: number }> {
  return Object.entries(values ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map(([name, weight]) => ({ name, weight }))
}

async function saveProfileStatement() {
  const description = profileStatement.value.trim()
  if (!description) return
  statementSaving.value = true
  statementStatus.value = ''
  try {
    const result = await submitMusicProfileStatement(description)
    musicProfile.value = result.analysis
    statementStatus.value = '收到，我会按这段状态调整推荐'
    await loadRecommendations()
  } catch (error) {
    statementStatus.value = error instanceof Error ? error.message : '这段话暂时没理解好'
  } finally {
    statementSaving.value = false
  }
}

function removeRecent(track: Track) {
  library.removeRecent(track)
}

function trackIdentity(track: Track): string {
  return `bili:${track.bvid}${track.cid != null ? `:cid:${track.cid}` : ''}`
}

function uniqueTracks(tracks: Track[]): Track[] {
  const map = new Map<string, Track>()
  for (const track of tracks) {
    const key = track.trackId ?? `${track.bvid}:${track.cid ?? 'video'}`
    if (!map.has(key)) map.set(key, track)
  }
  return [...map.values()]
}

</script>

<style scoped>
.page {
  padding: 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 32px;
}

.welcome {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.welcome h1 {
  font-size: 26px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.welcome .sub {
  margin-top: 6px;
  font-size: 14px;
  color: var(--color-text-secondary);
}

.agent-link {
  flex: 0 0 auto;
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 0 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  color: var(--color-primary);
  background: var(--color-primary-soft);
  font-size: 13px;
  font-weight: 700;
}

.agent-link:hover {
  border-color: var(--color-primary);
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 20px;
}

.pending-text,
.empty-text {
  font-size: 14px;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.recommend-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
}

.recommend-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 34px;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.recommend-main {
  min-width: 0;
  height: 72px;
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  padding: 8px;
  border-radius: var(--radius-small);
  color: var(--color-text-primary);
  text-align: left;
  transition: background 160ms ease;
}

.recommend-main:hover {
  background: var(--color-bg-hover);
}

.recommend-cover {
  width: 56px;
  height: 56px;
  border-radius: var(--radius-small);
  object-fit: cover;
  background: var(--color-bg-hover);
}

.recommend-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.recommend-copy strong,
.recommend-copy small {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.recommend-copy strong {
  font-size: 14px;
  font-weight: 600;
}

.recommend-copy small {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.recommend-dismiss {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-small);
  color: var(--color-text-tertiary);
  transition: background 160ms ease, color 160ms ease;
}

.recommend-dismiss:hover {
  background: var(--color-bg-hover);
  color: var(--color-primary);
}

.rank-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.rank-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 32px;
  align-items: center;
  gap: 6px;
  height: 58px;
  padding: 0 8px 0 0;
  border-radius: var(--radius-small);
  background: transparent;
  color: var(--color-text-primary);
  transition: background 160ms ease;
}

.rank-row:hover {
  background: var(--color-bg-hover);
}

.rank-main {
  min-width: 0;
  height: 58px;
  display: grid;
  grid-template-columns: 32px 44px minmax(0, 1fr) minmax(96px, 160px) 112px;
  align-items: center;
  gap: 12px;
  padding: 0 12px;
  border: none;
  background: transparent;
  color: var(--color-text-primary);
  cursor: pointer;
  text-align: left;
}

.rank-index {
  color: var(--color-primary);
  font-weight: 700;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.rank-cover {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-small);
  object-fit: cover;
  background: var(--color-bg-hover);
}

.rank-title,
.rank-owner {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.rank-title {
  font-size: 14px;
}

.rank-owner,
.rank-count {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.rank-count {
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.rank-remove {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
  opacity: 0;
  transition: opacity 160ms ease, background 160ms ease, color 160ms ease;
}

.rank-row:hover .rank-remove,
.rank-remove:focus-visible {
  opacity: 1;
}

.rank-remove:hover {
  background: var(--color-primary-soft);
  color: var(--color-primary);
}

.more-link {
  font-size: 13px;
  color: var(--color-text-secondary);
  text-decoration: none;
  transition: color 160ms ease;
}

.more-link:hover {
  color: var(--color-primary);
}

.profile-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.profile-panel {
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-bg-elevated);
}

.profile-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-bottom: 12px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.signal-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.signal-chip {
  max-width: 100%;
  min-height: 26px;
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  border-radius: var(--radius-small);
  font-size: 12px;
  color: var(--color-text-primary);
  background: var(--color-bg-hover);
  overflow-wrap: anywhere;
}

.signal-chip.positive {
  color: #116329;
  background: #e8f5eb;
}

.signal-chip.negative {
  color: #8a241f;
  background: #fdeceb;
}

.signal-chip.mood {
  color: #37558f;
  background: #edf2ff;
}

.reason-copy {
  color: var(--color-text-primary);
  font-size: 13px;
  line-height: 1.65;
}

.trace-list {
  margin: 12px 0 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.trace-list li {
  min-width: 0;
  font-size: 13px;
  color: var(--color-text-primary);
}

.trace-list span,
.trace-list small {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trace-list small {
  margin-top: 2px;
  font-size: 11px;
  color: var(--color-text-secondary);
}

.statement-form {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.statement-form textarea {
  width: 100%;
  min-height: 92px;
  resize: vertical;
  padding: 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-small);
  background: var(--color-bg);
  color: var(--color-text-primary);
  font: inherit;
  font-size: 13px;
  line-height: 1.5;
}

.statement-form textarea:focus {
  outline: 2px solid var(--color-primary-soft);
  border-color: var(--color-primary);
}

.statement-actions {
  min-height: 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.statement-actions span {
  min-width: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.statement-actions button {
  flex: 0 0 auto;
  min-width: 84px;
  height: 32px;
  padding: 0 12px;
  border-radius: var(--radius-small);
  background: var(--color-primary);
  color: white;
  font-size: 13px;
  font-weight: 600;
}

.statement-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

@media (max-width: 720px) {
  .page {
    padding: 20px;
  }

  .welcome {
    flex-direction: column;
  }

  .profile-grid {
    grid-template-columns: 1fr;
  }

  .statement-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .statement-actions button {
    width: 100%;
  }

  .rank-row {
    grid-template-columns: minmax(0, 1fr) 32px;
  }

  .rank-main {
    grid-template-columns: 28px 40px minmax(0, 1fr) 96px;
  }

  .rank-owner {
    display: none;
  }
}
</style>
