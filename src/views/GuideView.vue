<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '../components/common/AppIcon.vue'
import { useAuthStore } from '../stores/auth'
import { availableGuides, GUIDES, guideDownloads, loadGuide } from '../docs/guides'

/**
 * Руководство внутри приложения: тот же документ, что в PDF и DOCX (docs/manuals/*.md), с
 * оглавлением, поиском по тексту разделов, ссылками на разделы (/guide#раздел) и увеличением
 * снимков экрана. /guide — руководство пользователя, /guide/admin — администратора.
 */
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const kind = computed(() => (GUIDES[route.meta.guide] ? route.meta.guide : 'user'))
const info = computed(() => GUIDES[kind.value])
const files = computed(() => guideDownloads(kind.value))
const guides = computed(() => availableGuides(auth))

const guide = shallowRef(null)
const loadError = ref(null)
const rootEl = ref(null)
const articleEl = ref(null)
const tocEl = ref(null)
const zoomCloseEl = ref(null)
const query = ref('')
const activeId = ref('')
const zoomed = ref(null)

const headings = computed(() => guide.value?.headings || [])
const tocHeadings = computed(() => headings.value.filter((heading) => heading.level <= 2))
const subtitle = computed(() => [guide.value?.meta.subtitle, guide.value?.meta.date].filter(Boolean).join(' · '))
const needle = computed(() => query.value.trim().toLowerCase())
const sectionText = computed(() => {
  const texts = {}
  for (const [id, text] of Object.entries(guide.value?.sections || {})) texts[id] = text.toLowerCase()
  return texts
})

// Поиск — по заголовкам и тексту разделов; у найденного в тексте — фрагмент вокруг совпадения
const results = computed(() => {
  const value = needle.value
  if (!value) return []
  const found = []
  for (const heading of headings.value) {
    if (heading.level > 3) continue
    const inTitle = heading.text.toLowerCase().includes(value)
    const at = sectionText.value[heading.id]?.indexOf(value) ?? -1
    if (!inTitle && at === -1) continue
    let snippet = null
    if (!inTitle) {
      const text = guide.value.sections[heading.id]
      const from = Math.max(0, at - 40)
      const to = Math.min(text.length, at + value.length + 60)
      snippet = {
        before: (from > 0 ? '…' : '') + text.slice(from, at),
        match: text.slice(at, at + value.length),
        after: text.slice(at + value.length, to) + (to < text.length ? '…' : ''),
      }
    }
    found.push({ ...heading, snippet })
  }
  return found
})

const activeTocId = computed(() => {
  // В оглавлении подсвечивается раздел, в который входит текущий подраздел
  const list = headings.value
  for (let i = list.findIndex((heading) => heading.id === activeId.value); i >= 0; i--) if (list[i].level <= 2) return list[i].id
  return tocHeadings.value[0]?.id || ''
})

function hashId() {
  const raw = route.hash.replace(/^#/, '')
  try { return decodeURIComponent(raw) } catch { return raw }
}

function scrollContainer() {
  return rootEl.value?.closest('.app-main') || document.scrollingElement
}

function headingEl(id) {
  if (!id || !articleEl.value) return null
  return articleEl.value.querySelector(`[id="${CSS.escape(id)}"]`)
}

// Снимки экрана загружаются по мере прокрутки и раздвигают текст выше раздела: после перехода к
// разделу его положение поправляется, пока читатель не начал прокручивать сам.
const PIN_MS = 4000
let pinned = null
// Свои переходы по #раздел отличаем от «назад/вперёд» в браузере
let ownNavigations = 0

function isPinned() {
  if (pinned && Date.now() > pinned.until) pinned = null
  return Boolean(pinned)
}

function go(id) {
  const target = headingEl(id)
  if (!target) return false
  target.scrollIntoView({ block: 'start' })
  pinned = { id, until: Date.now() + PIN_MS }
  activeId.value = id
  if (hashId() !== id) {
    ownNavigations++
    router.replace({ path: route.path, query: route.query, hash: `#${id}` }).catch(() => {}).finally(() => { ownNavigations-- })
  }
  return true
}

function repin() {
  if (isPinned()) headingEl(pinned.id)?.scrollIntoView({ block: 'start' })
}

function unpin() { pinned = null }

const SCROLL_KEYS = new Set(['PageUp', 'PageDown', 'ArrowUp', 'ArrowDown', 'Home', 'End', ' '])

function onKeydown(event) {
  if (zoomed.value) {
    if (event.key === 'Escape') { event.preventDefault(); closeZoom() }
    return
  }
  if (SCROLL_KEYS.has(event.key)) unpin()
}

function onArticleClick(event) {
  const link = event.target.closest('a[href^="#"]')
  if (link) {
    event.preventDefault()
    const id = link.getAttribute('href').slice(1)
    try { go(decodeURIComponent(id)) } catch { go(id) }
    return
  }
  const image = event.target.closest('figure img')
  if (image) openZoom(image)
}

let zoomReturnFocus = null
async function openZoom(image) {
  zoomReturnFocus = document.activeElement
  zoomed.value = { src: image.getAttribute('src'), alt: image.getAttribute('alt') || '' }
  await nextTick()
  zoomCloseEl.value?.focus()
}
function closeZoom() {
  zoomed.value = null
  zoomReturnFocus?.focus?.()
  zoomReturnFocus = null
}

function onSearchKeydown(event) {
  if (event.key === 'Escape' && query.value) { event.stopPropagation(); query.value = '' }
  if (event.key === 'Enter' && results.value.length) go(results.value[0].id)
}

function switchGuide(key) {
  if (key !== kind.value) router.push(GUIDES[key].path)
}

// Найденное подсвечивается и в тексте — CSS Custom Highlight API, без изменения разметки
const HIGHLIGHT = 'guide-search'
const canHighlight = typeof CSS !== 'undefined' && 'highlights' in CSS && typeof Highlight !== 'undefined'
function highlightMatches() {
  if (!canHighlight) return
  CSS.highlights.delete(HIGHLIGHT)
  const value = needle.value
  if (value.length < 2 || !articleEl.value) return
  const ranges = []
  const walker = document.createTreeWalker(articleEl.value, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node && ranges.length < 1000; node = walker.nextNode()) {
    const text = node.nodeValue.toLowerCase()
    if (text.length !== node.nodeValue.length) continue
    for (let at = text.indexOf(value); at !== -1; at = text.indexOf(value, at + value.length)) {
      const range = new Range()
      range.setStart(node, at)
      range.setEnd(node, at + value.length)
      ranges.push(range)
    }
  }
  if (ranges.length) CSS.highlights.set(HIGHLIGHT, new Highlight(...ranges))
}
watch(needle, highlightMatches)

// Пункт оглавления текущего раздела остаётся виден в самом оглавлении (прокрутка только его)
watch(activeTocId, async () => {
  await nextTick()
  const toc = tocEl.value
  const item = toc?.querySelector('li.active')
  if (!toc || !item || query.value) return
  const top = item.offsetTop
  if (top < toc.scrollTop + 40 || top + item.offsetHeight > toc.scrollTop + toc.clientHeight - 40) {
    toc.scrollTop = Math.max(0, top - toc.clientHeight / 3)
  }
})

// Раздел меняется и без щелчка по оглавлению: назад/вперёд в браузере, правка адреса
watch(() => route.hash, () => {
  const id = hashId()
  if (!ownNavigations && id && id !== activeId.value) go(id)
})

let observer = null
let endObserver = null
let resizeObserver = null
let scroller = null

function updateActive() {
  // Сразу после перехода текущий — выбранный раздел, даже если он в конце документа и не
  // поднялся к верху экрана
  if (isPinned()) return
  // Иначе текущий — последний заголовок, поднявшийся в верхнюю треть экрана, а в самом
  // конце документа — последний видимый
  const atEnd = articleEl.value && articleEl.value.getBoundingClientRect().bottom <= window.innerHeight + 1
  const limit = window.innerHeight * (atEnd ? 1 : 0.3)
  let current = headings.value[0]?.id || ''
  for (const el of articleEl.value?.querySelectorAll('h2[id], h3[id], h4[id], h5[id]') || []) {
    if (el.getBoundingClientRect().top - 1 > limit) break
    current = el.id
  }
  activeId.value = current
}

function watchLayout() {
  const elements = articleEl.value?.querySelectorAll('h2[id], h3[id], h4[id], h5[id]') || []
  if (typeof IntersectionObserver !== 'undefined') {
    observer = new IntersectionObserver(updateActive, { rootMargin: '0px 0px -70% 0px' })
    for (const el of elements) observer.observe(el)
    // Конец документа: последние разделы не поднимаются к верху экрана
    endObserver = new IntersectionObserver(updateActive, { threshold: 1 })
    if (articleEl.value?.lastElementChild) endObserver.observe(articleEl.value.lastElementChild)
  }
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(repin)
    resizeObserver.observe(articleEl.value)
  }
}

async function load() {
  loadError.value = null
  try {
    guide.value = await loadGuide(kind.value)
  } catch (err) {
    loadError.value = err
    return
  }
  activeId.value = guide.value.headings[0]?.id || ''
  await nextTick()
  watchLayout()
  if (!go(hashId())) scrollContainer()?.scrollTo({ top: 0 })
  highlightMatches()
}

onMounted(() => {
  scroller = scrollContainer()
  for (const type of ['wheel', 'touchmove', 'pointerdown']) scroller?.addEventListener(type, unpin, { passive: true })
  window.addEventListener('keydown', onKeydown)
  load()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  endObserver?.disconnect()
  resizeObserver?.disconnect()
  for (const type of ['wheel', 'touchmove', 'pointerdown']) scroller?.removeEventListener(type, unpin)
  window.removeEventListener('keydown', onKeydown)
  if (canHighlight) CSS.highlights.delete(HIGHLIGHT)
})
</script>

<template>
  <div ref="rootEl" class="page guide">
    <header class="guide-header">
      <span class="guide-mark" aria-hidden="true"><AppIcon name="book" :size="20" /></span>
      <div class="guide-titles">
        <h1 class="page-title">{{ info.title }}</h1>
        <div class="page-subtitle">Пульс Проекта<template v-if="subtitle"> · {{ subtitle }}</template></div>
      </div>
      <div v-if="guides.length > 1" class="segmented guide-switch" role="group" aria-label="Руководство">
        <button
          v-for="item in guides" :key="item.key" type="button" :class="{ 'is-active': item.key === kind }"
          :aria-pressed="item.key === kind" :data-testid="`guide-switch-${item.key}`" @click="switchGuide(item.key)"
        >{{ item.short }}</button>
      </div>
      <div class="spacer" />
      <div class="guide-actions">
        <a class="btn" :href="files.pdfUrl" target="_blank" rel="noopener" data-testid="guide-open-pdf"><AppIcon name="external" :size="14" />Открыть PDF</a>
        <a class="btn btn-primary" :href="files.pdfUrl" :download="files.pdfName" data-testid="guide-download-pdf"><AppIcon name="download" :size="14" />Скачать PDF</a>
        <a class="btn" :href="files.docxUrl" :download="files.docxName" data-testid="guide-download-docx"><AppIcon name="file" :size="14" />Скачать DOCX</a>
      </div>
    </header>

    <div v-if="loadError" class="card empty-state">
      <h3>Не удалось открыть руководство</h3>
      <p>Проверьте связь и попробуйте ещё раз — или скачайте PDF.</p>
      <button class="btn btn-primary" type="button" @click="load">Повторить</button>
    </div>

    <div v-else class="guide-layout">
      <nav ref="tocEl" class="guide-toc card scroll-thin" aria-label="Содержание руководства">
        <label class="guide-search">
          <AppIcon name="search" :size="14" class="guide-search-icon" />
          <input
            v-model="query" class="input input-sm" type="search" placeholder="Найти в руководстве"
            aria-label="Найти в руководстве" data-testid="guide-search" @keydown="onSearchKeydown"
          />
        </label>
        <template v-if="needle">
          <p class="guide-toc-caption" aria-live="polite">{{ results.length ? `Найдено разделов: ${results.length}` : 'Ничего не найдено' }}</p>
          <ul class="guide-toc-list">
            <li v-for="heading in results" :key="heading.id" :class="`toc-level-${heading.level}`">
              <a :href="`#${heading.id}`" @click.prevent="go(heading.id)">
                <span class="toc-text">{{ heading.text }}</span>
                <span v-if="heading.snippet" class="toc-snippet">{{ heading.snippet.before }}<mark>{{ heading.snippet.match }}</mark>{{ heading.snippet.after }}</span>
              </a>
            </li>
          </ul>
        </template>
        <ul v-else class="guide-toc-list">
          <li v-for="heading in tocHeadings" :key="heading.id" :class="[`toc-level-${heading.level}`, { active: heading.id === activeTocId }]">
            <a :href="`#${heading.id}`" :aria-current="heading.id === activeTocId ? 'location' : undefined" @click.prevent="go(heading.id)">{{ heading.text }}</a>
          </li>
        </ul>
      </nav>

      <div class="guide-main">
        <select v-if="guide" class="select guide-toc-mobile" :value="activeTocId" aria-label="Перейти к разделу" @change="go($event.target.value)">
          <option v-for="heading in tocHeadings" :key="heading.id" :value="heading.id">{{ heading.level === 2 ? '\u2003' : '' }}{{ heading.text }}</option>
        </select>
        <div v-if="!guide" class="card guide-content guide-loading" aria-busy="true">
          <span class="guide-skeleton w60" /><span class="guide-skeleton" /><span class="guide-skeleton w80" /><span class="guide-skeleton w40" />
        </div>
        <!-- Содержимое — собственный документ приложения; текст экранирован, HTML из исходника не вставляется (utils/markdown) -->
        <article
          v-else ref="articleEl" class="card guide-content" data-testid="guide-content"
          @click="onArticleClick" @load.capture="repin" v-html="guide.html"
        />
      </div>
    </div>

    <Teleport to="body">
      <div v-if="zoomed" class="guide-zoom" role="dialog" aria-modal="true" :aria-label="zoomed.alt || 'Снимок экрана'" @click="closeZoom">
        <button ref="zoomCloseEl" class="btn btn-icon guide-zoom-close" type="button" aria-label="Закрыть" @click.stop="closeZoom"><AppIcon name="close" /></button>
        <img :src="zoomed.src" :alt="zoomed.alt" />
        <p v-if="zoomed.alt">{{ zoomed.alt }}</p>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.guide { max-width: 1220px; }
.guide-header { display: flex; align-items: center; gap: 12px 14px; flex-wrap: wrap; margin-bottom: 18px; }
.guide-mark { display: inline-flex; align-items: center; justify-content: center; width: 40px; height: 40px; border-radius: var(--radius-md); background: var(--color-primary-soft); color: var(--color-primary-dark); flex-shrink: 0; }
.guide-titles { min-width: 0; }
.guide-switch { margin-left: 6px; }
.guide-actions { display: flex; gap: 8px; flex-wrap: wrap; }

.guide-layout { display: grid; grid-template-columns: 264px minmax(0, 1fr); gap: 24px; align-items: start; }
.guide-toc { position: sticky; top: 16px; max-height: calc(100vh - 32px); overflow-y: auto; padding: 10px 8px 12px; }
@supports (height: 100dvh) { .guide-toc { max-height: calc(100dvh - 32px); } }
.guide-search { position: sticky; top: 0; display: block; margin: -10px -8px 6px; padding: 10px 8px 6px; background: var(--color-surface); z-index: 1; }
.guide-search .input { padding-left: 27px; }
.guide-search-icon { position: absolute; left: 17px; top: 16px; color: var(--color-text-muted); pointer-events: none; }
.guide-toc-caption { margin: 4px 8px 6px; font-size: 11.5px; color: var(--color-text-muted); }
.guide-toc-list { list-style: none; margin: 0; padding: 0; }
.guide-toc-list a { display: block; padding: 5px 8px; border-radius: var(--radius-sm); color: var(--color-text); text-decoration: none; font-size: 12.5px; line-height: 1.35; }
.guide-toc-list a:hover { background: var(--color-hover); }
.toc-level-1 { margin-top: 4px; }
.toc-level-1 > a { font-weight: 600; }
.toc-level-2 > a { padding-left: 20px; color: var(--color-text-secondary); }
.toc-level-3 > a { padding-left: 30px; color: var(--color-text-secondary); font-size: 12px; }
.guide-toc-list li.active > a { background: var(--color-primary-soft); color: var(--color-primary-dark); }
.toc-text { display: block; }
.toc-snippet { display: block; margin-top: 2px; font-size: 11.5px; font-weight: 400; color: var(--color-text-muted); }
.toc-snippet mark { background: #ffe9a8; color: inherit; border-radius: 2px; }
.guide-toc-mobile { display: none; }

.guide-content { padding: 4px 40px 40px; font-size: 14.5px; line-height: 1.65; color: var(--color-text); overflow-wrap: break-word; }
.guide-content :deep(h2) { position: relative; font-size: 21px; line-height: 1.3; margin: 34px 0 12px; padding-top: 24px; border-top: 1px solid var(--color-border); scroll-margin-top: 16px; }
.guide-content :deep(h2:first-child) { border-top: 0; padding-top: 0; margin-top: 26px; }
.guide-content :deep(h3) { position: relative; font-size: 17px; line-height: 1.35; margin: 28px 0 10px; scroll-margin-top: 16px; }
.guide-content :deep(h4) { position: relative; font-size: 15px; margin: 22px 0 8px; scroll-margin-top: 16px; }
.guide-content :deep(h5) { position: relative; font-size: 14px; margin: 18px 0 6px; scroll-margin-top: 16px; }
.guide-content :deep(.md-anchor) { margin-left: 8px; color: var(--color-text-muted); text-decoration: none; font-weight: 400; opacity: 0; transition: opacity 0.12s; }
.guide-content :deep(:is(h2, h3, h4, h5):hover .md-anchor) { opacity: 0.7; }
.guide-content :deep(.md-anchor:hover) { opacity: 1; color: var(--color-primary); }
.guide-content :deep(p) { margin: 0 0 12px; }
.guide-content :deep(ul), .guide-content :deep(ol) { margin: 0 0 14px; padding-left: 24px; }
.guide-content :deep(li) { margin: 4px 0; }
.guide-content :deep(li > ul), .guide-content :deep(li > ol) { margin: 4px 0 0; }
.guide-content :deep(li::marker) { color: var(--color-text-muted); }
.guide-content :deep(strong) { font-weight: 600; }
.guide-content :deep(code) { background: var(--color-hover); border: 1px solid var(--color-border); border-radius: 4px; padding: 0 5px; font-size: 0.86em; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; overflow-wrap: anywhere; }
.guide-content :deep(pre) { background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: var(--radius-sm); padding: 10px 14px; overflow-x: auto; margin: 0 0 14px; }
.guide-content :deep(pre code) { background: none; border: 0; padding: 0; overflow-wrap: normal; }
.guide-content :deep(a) { color: var(--color-primary-dark); }
.guide-content :deep(blockquote) { margin: 14px 0 16px; padding: 10px 16px; border-left: 3px solid var(--color-border-strong); background: var(--color-surface-2); border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }
.guide-content :deep(.md-callout-tip) { border-left-color: var(--color-primary); background: #f3f6ff; }
.guide-content :deep(.md-callout-important) { border-left-color: var(--color-warning); background: #fffaf0; }
.guide-content :deep(blockquote > :last-child) { margin-bottom: 0; }
.guide-content :deep(.md-table) { overflow-x: auto; margin: 0 0 16px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); }
.guide-content :deep(table) { border-collapse: collapse; width: 100%; font-size: 13px; line-height: 1.5; }
.guide-content :deep(th), .guide-content :deep(td) { border-bottom: 1px solid var(--color-border); padding: 7px 12px; text-align: left; vertical-align: top; }
.guide-content :deep(th + th), .guide-content :deep(td + td) { border-left: 1px solid var(--color-border); }
.guide-content :deep(tbody tr:last-child td) { border-bottom: 0; }
.guide-content :deep(th) { background: var(--color-surface-2); font-weight: 600; color: var(--color-text-secondary); }
.guide-content :deep(.md-align-center) { text-align: center; }
.guide-content :deep(.md-align-right) { text-align: right; }
.guide-content :deep(figure) { margin: 18px 0 22px; text-align: center; }
/* Снимки экрана — не выше трёх четвертей экрана; щелчок увеличивает */
.guide-content :deep(figure img) { display: inline-block; max-width: 100%; max-height: 75vh; width: auto; height: auto; border: 1px solid var(--color-border); border-radius: var(--radius-md); box-shadow: var(--shadow-2); cursor: zoom-in; background: var(--color-surface-2); }
.guide-content :deep(figcaption) { margin-top: 8px; font-size: 12.5px; color: var(--color-text-muted); }
.guide-content :deep(hr) { border: 0; border-top: 1px solid var(--color-border); margin: 22px 0; }

.guide-loading { display: flex; flex-direction: column; gap: 12px; padding-top: 32px; }
.guide-skeleton { display: block; height: 12px; border-radius: 6px; background: var(--color-hover); width: 100%; }
.guide-skeleton.w80 { width: 80%; }
.guide-skeleton.w60 { width: 60%; height: 18px; }
.guide-skeleton.w40 { width: 40%; }

.guide-zoom { position: fixed; inset: 0; z-index: 600; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; padding: 48px 24px 24px; background: rgba(16, 20, 30, 0.86); cursor: zoom-out; animation: fade-in 0.12s ease; }
.guide-zoom img { max-width: 100%; max-height: calc(100% - 36px); border-radius: var(--radius-md); background: #fff; box-shadow: var(--shadow-3); }
.guide-zoom p { margin: 0; color: #fff; font-size: 13px; }
.guide-zoom-close { position: absolute; top: 12px; right: 12px; background: rgba(255, 255, 255, 0.12); border-color: transparent; color: #fff; }
.guide-zoom-close:hover:not(:disabled) { background: rgba(255, 255, 255, 0.24); border-color: transparent; }

@media (max-width: 900px) {
  .guide-layout { grid-template-columns: minmax(0, 1fr); }
  .guide-toc { display: none; }
  .guide-toc-mobile { display: block; position: sticky; top: 0; z-index: 2; margin-bottom: 12px; box-shadow: var(--shadow-1); }
  .guide-content { padding: 4px 18px 26px; font-size: 14px; }
  .guide-content :deep(:is(h2, h3, h4, h5)) { scroll-margin-top: 52px; }
  .guide-actions { width: 100%; }
  .guide-actions .btn { flex: 1; min-height: 38px; }
}

@media print {
  .guide-toc, .guide-actions, .guide-switch, .guide-toc-mobile { display: none; }
  .guide-layout { display: block; }
  .guide-content { border: 0; box-shadow: none; padding: 0; }
}
</style>

<style>
/* Совпадения поиска в тексте руководства (CSS Custom Highlight API) */
::highlight(guide-search) { background-color: #ffe08a; color: inherit; }
</style>
