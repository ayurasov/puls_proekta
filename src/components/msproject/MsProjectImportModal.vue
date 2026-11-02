<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../../api/client'
import { pluralize } from '../../utils/format'
import BaseModal from '../common/BaseModal.vue'
import HelpLink from '../common/HelpLink.vue'
import AppIcon from '../common/AppIcon.vue'
import SegmentedControl from '../common/SegmentedControl.vue'
import ToggleSwitch from '../common/ToggleSwitch.vue'

/**
 * Загрузка файла MS Project (ADR-0012): новым проектом, новым планом проекта, заменой плана или обновлением плана,
 * который ведётся в MS Project. Сначала «Проверить» — план загрузки без записи, затем «Применить». Файл остаётся в
 * браузере: смена параметров пересчитывает план загрузки без повторного выбора файла.
 */
const props = defineProps({
  target: { type: String, required: true },          // project | plan | replace | update
  projectId: { type: Number, default: null },
  planId: { type: Number, default: null },
  planTitle: { type: String, default: '' },
  directions: { type: Array, default: () => [] },
  directionId: { type: Number, default: null },
  master: { type: Boolean, default: false },         // план уже ведётся в MS Project
  // Перед заменой и обновлением открытого плана — отправить его неотправленные правки; false — не удалось
  beforeApply: { type: Function, default: null },
})
// Как MAX_CONTENT_LENGTH backend и client_max_body_size nginx (руководство по развёртыванию)
const MAX_BYTES = 25 * 1024 * 1024
const emit = defineEmits(['close', 'done'])

const TITLES = {
  project: 'Проект из MS Project', plan: 'План из MS Project', replace: 'Заменить план из MS Project',
  update: 'Обновить из MS Project',
}
const MODES = [
  { value: 'once', label: 'Разовый перенос' },
  { value: 'master', label: 'Мастер-копия', title: 'График ведётся в MS Project, «Пульс» обновляется из файла' },
]
const DURATIONS = [
  { value: 'dates', label: 'По датам файла' },
  { value: 'working', label: 'В рабочих днях' },
]
const BASELINES = [
  { value: 'current', label: 'Текущие сроки' },
  { value: 'baseline', label: 'По базовому плану' },
]

const file = ref(null)
const dragOver = ref(false)
const options = ref({
  mode: props.master ? 'master' : 'once',
  durations: 'dates', baseline: 'current', categoryField: '', deleteMissing: false,
})
const title = ref('')
const directionId = ref(props.directionId ?? '')
const preview = ref(null)
const previewFor = ref('')
const busy = ref('')
const error = ref('')
// Конфликт при применении (план изменился, файл другой) — остаётся на экране, пока не применят свежий план
const notice = ref('')
// Настраиваемые поля последнего успешного плана загрузки: выбор категории не пропадает после ошибки проверки
const customFields = ref([])
const titleTouched = ref(false)
const fileInput = ref(null)

const optionsKey = computed(() => JSON.stringify(options.value))
const fresh = computed(() => preview.value && previewFor.value === `${file.value?.name}:${file.value?.size}:${optionsKey.value}`)
const stats = computed(() => preview.value?.stats || {})
const changes = computed(() => preview.value?.changes || null)
const linkedUsers = computed(() => (preview.value?.assignees || []).filter((a) => a.userId).length)

function pick(event) {
  const chosen = event.target.files?.[0] || event.dataTransfer?.files?.[0]
  dragOver.value = false
  // Тот же файл можно выбрать снова (например, пересохранённый в MS Project)
  if (event.target?.value !== undefined) event.target.value = ''
  if (!chosen) return
  file.value = chosen
  preview.value = null
  notice.value = ''
  error.value = ''
  if (!titleTouched.value) title.value = ''
  if (chosen.size > MAX_BYTES) {
    error.value = `Файл больше 25 МБ (${(chosen.size / 1024 / 1024).toFixed(1)} МБ) — такой загрузки сервер не примет. Удалите из файла лишнее (базовые планы, неиспользуемые ресурсы) или попросите администратора увеличить предел`
    return
  }
  check()
}

function form(extra = {}) {
  const data = new FormData()
  data.append('file', file.value)
  data.append('target', props.target)
  data.append('options', JSON.stringify({ ...options.value, categoryField: options.value.categoryField || null }))
  if (props.projectId) data.append('projectId', String(props.projectId))
  if (props.planId) data.append('planId', String(props.planId))
  for (const [key, value] of Object.entries(extra)) if (value !== null && value !== undefined && value !== '') data.append(key, String(value))
  return data
}

let seq = 0
async function check() {
  if (!file.value) return
  const id = ++seq
  busy.value = 'check'
  error.value = ''
  const key = `${file.value.name}:${file.value.size}:${optionsKey.value}`
  try {
    const res = await api.upload('/import/msproject/preview', form(), 'POST')
    if (id !== seq) return
    preview.value = res
    previewFor.value = key
    customFields.value = res.customFields || []
    if (!title.value) title.value = res.project?.title || res.project?.name || ''
  } catch (err) {
    if (id === seq) { error.value = err.message; preview.value = null }
  } finally {
    if (id === seq) busy.value = ''
  }
}

// Параметры поменялись — план загрузки пересчитывается сам (файл уже в браузере)
let timer = 0
watch(optionsKey, () => {
  if (!file.value) return
  clearTimeout(timer)
  timer = setTimeout(check, 250)
})
onBeforeUnmount(() => clearTimeout(timer))

async function apply() {
  if (!fresh.value) return
  busy.value = 'apply'
  error.value = ''
  notice.value = ''
  if (props.beforeApply && !(await props.beforeApply())) {
    busy.value = ''
    error.value = 'В открытом плане есть неотправленные изменения — дождитесь их сохранения и повторите'
    return
  }
  try {
    const res = await api.upload('/import/msproject/apply', form({
      fingerprint: preview.value.fingerprint, planRevision: preview.value.planRevision,
      title: props.target === 'project' || props.target === 'plan' ? title.value.trim() : '',
      directionId: props.target === 'project' ? directionId.value : '',
    }), 'POST')
    emit('done', res)
  } catch (err) {
    if (err.status === 409) {
      // План изменился или файл другой: прежний план загрузки больше не годится — показать свежий
      notice.value = `${err.message}. Ниже — новый план загрузки: проверьте его и примените снова.`
      previewFor.value = ''
      busy.value = ''
      check()
      return
    }
    error.value = err.message
  } finally {
    if (busy.value === 'apply') busy.value = ''
  }
}

function fieldLabel(name) {
  return { name: 'Название', start: 'Начало', end: 'Окончание', date: 'Дата', progress: 'Прогресс', status: 'Статус',
    parentKey: 'Группа', position: 'Порядок', assigneeKey: 'Исполнитель', deadline: 'Крайний срок',
    constraintType: 'Ограничение', constraintDate: 'Дата ограничения', completedDate: 'Завершена', duration: 'Длительность',
    endMode: 'Окончание задаётся', offset: 'Сдвиг', category: 'Категория', jiraUrl: 'Jira' }[name] || name
}
function value(v) {
  if (v === null || v === undefined || v === '') return '—'
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(v))) return String(v).split('-').reverse().join('.')
  return String(v)
}
const sizeText = computed(() => (file.value ? `${(file.value.size / 1024 / 1024).toFixed(file.value.size > 1024 * 1024 ? 1 : 2)} МБ` : ''))
</script>

<template>
  <BaseModal :title="TITLES[target]" size="wide" data-testid="msp-modal" @close="emit('close')">
    <template #header-actions><HelpLink topic="msproject" /></template>

    <section class="sec">
      <label
        class="drop" :class="{ over: dragOver, chosen: file }" data-testid="msp-drop"
        @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop.prevent="pick"
      >
        <input ref="fileInput" type="file" accept=".xml,.mpp,application/xml,text/xml" class="sr-only" data-testid="msp-file" @change="pick" />
        <AppIcon name="upload" :size="22" />
        <span v-if="file" class="drop-main"><strong>{{ file.name }}</strong> · {{ sizeText }}</span>
        <span v-else class="drop-main"><strong>Выберите файл</strong> или перетащите его сюда</span>
        <span class="muted small">В MS Project: «Файл → Сохранить как → XML (*.xml)». Файл .mpp сохраните так же.</span>
      </label>
    </section>

    <section class="sec">
      <div class="sec-title">Как вести план дальше</div>
      <SegmentedControl v-model="options.mode" block :options="MODES" aria-label="Как вести план" data-testid="msp-mode" />
      <p class="hint">
        <template v-if="options.mode === 'once'">План дальше ведётся в «Пульсе»; файл больше не нужен.</template>
        <template v-else>График ведётся в MS Project, «Пульс» обновляется из файла — «Обновить из MS Project» в меню вкладки
          плана. Комментарии, пометки, ссылки Jira и цвета «Пульса» при обновлении сохраняются.</template>
      </p>
    </section>

    <section class="sec grid">
      <div>
        <div class="sec-title">Длительности</div>
        <SegmentedControl v-model="options.durations" block :options="DURATIONS" aria-label="Длительности" />
        <p class="hint">{{ options.durations === 'dates' ? 'Даты — как в файле, день в день.' : 'Длительность — в рабочих днях по календарю «Пульса»; даты могут отличаться от файла.' }}</p>
      </div>
      <div>
        <div class="sec-title">Сроки</div>
        <SegmentedControl v-model="options.baseline" block :options="BASELINES" aria-label="Сроки" />
        <p class="hint">{{ options.baseline === 'current' ? 'Сроки задач — текущие из файла.' : 'Сроки — из базового плана, отставание от него — сдвигом на полосе.' }}</p>
      </div>
      <label v-if="customFields.length" class="field">
        <span class="field-label">Категории из поля</span>
        <select v-model="options.categoryField" class="select" data-testid="msp-category">
          <option value="">— не переносить —</option>
          <option v-for="f in customFields" :key="f.fieldId" :value="String(f.fieldId)">{{ f.label || f.alias || f.fieldName }} · {{ f.count }}</option>
        </select>
      </label>
      <template v-if="target === 'project' || target === 'plan'">
        <label class="field">
          <span class="field-label">{{ target === 'project' ? 'Название проекта' : 'Название плана' }}</span>
          <input v-model="title" class="input" maxlength="500" data-testid="msp-title" @input="titleTouched = true" />
        </label>
        <label v-if="target === 'project'" class="field">
          <span class="field-label">Направление</span>
          <select v-model="directionId" class="select">
            <option value="">Без направления</option>
            <option v-for="d in directions" :key="d.id" :value="d.id">{{ d.name }}</option>
          </select>
        </label>
      </template>
      <ToggleSwitch v-if="target === 'update'" v-model="options.deleteMissing" label="Удалить строки, которых нет в файле"
                    hint="Строки, добавленные в «Пульсе», не удаляются никогда; удалённые можно вернуть из корзины плана" />
    </section>

    <p v-if="notice" class="notice" role="alert" data-testid="msp-notice"><AppIcon name="warning" :size="14" /> {{ notice }}</p>
    <p v-if="error" class="error" role="alert">
      <AppIcon name="warning" :size="14" /> {{ error }}
      <button v-if="file && file.size <= MAX_BYTES && !busy" class="btn btn-sm" type="button" data-testid="msp-retry" @click="check">Проверить ещё раз</button>
    </p>
    <p v-if="busy === 'check'" class="muted small">Проверяем файл…</p>

    <template v-if="preview">
      <section class="sec summary" data-testid="msp-preview">
        <div class="sec-title">{{ preview.project?.name || 'Проект' }} <span v-if="preview.project?.start" class="muted">· {{ value(preview.project.start) }} — {{ value(preview.project.finish) }}</span></div>
        <div class="chips">
          <span class="chip">Групп {{ stats.groups }}</span>
          <span class="chip">Задач {{ stats.tasks }}</span>
          <span class="chip">Вех {{ stats.milestones }}</span>
          <span class="chip">Связей {{ stats.links }}</span>
          <span class="chip">Исполнителей {{ stats.assignees }}<template v-if="stats.assignees"> · связаны с сотрудниками {{ linkedUsers }}</template></span>
        </div>
        <p v-if="target === 'replace'" class="note">
          Сейчас в плане {{ preview.replacing }} {{ pluralize(preview.replacing, 'строка', 'строки', 'строк') }} — они уйдут в корзину
          плана, перед заменой сохранится версия «До импорта».
        </p>
      </section>

      <section v-if="changes" class="sec" data-testid="msp-changes">
        <div class="sec-title">Изменения</div>
        <div class="chips">
          <span class="chip new">Новых {{ changes.new.length }}</span>
          <span class="chip changed">Изменено {{ changes.changed.length }}</span>
          <span class="chip missing">Нет в файле {{ changes.missing.length }}</span>
          <span class="chip">Связи: +{{ changes.links.added.length }} −{{ changes.links.removed.length }}</span>
          <span class="chip muted">Без изменений {{ changes.unchanged }}</span>
        </div>
        <div v-if="changes.changed.length" class="list scroll-thin">
          <div v-for="row in changes.changed.slice(0, 100)" :key="row.key" class="change-row">
            <strong class="ellipsis">{{ row.name }}</strong>
            <span v-for="(diff, field) in row.fields || {}" :key="field" class="change small">
              <span class="muted">{{ fieldLabel(field) }}:</span> <span class="from">{{ value(diff.before) }}</span> → {{ value(diff.after) }}
            </span>
          </div>
        </div>
        <p v-if="changes.missing.length" class="muted small">
          Нет в файле: {{ changes.missing.slice(0, 10).map((r) => r.name).join(', ') }}<template v-if="changes.missing.length > 10"> и ещё {{ changes.missing.length - 10 }}</template>.
          {{ options.deleteMissing ? 'Они будут удалены.' : 'Они останутся в плане.' }}
        </p>
      </section>

      <section v-if="preview.warnings?.length" class="sec">
        <div class="sec-title">Что учесть</div>
        <ul class="warnings" data-testid="msp-warnings">
          <li v-for="w in preview.warnings" :key="w.code">
            {{ w.message }}<template v-if="w.count > 1"> — {{ w.count }}</template><span v-if="w.examples?.length" class="muted small">: {{ w.examples.join(', ') }}</span>
          </li>
        </ul>
      </section>

      <details v-if="!changes && preview.tree?.length" class="sec tree">
        <summary class="sec-title">Строки плана<template v-if="preview.truncated"> (первые {{ preview.tree.length }})</template></summary>
        <div class="list scroll-thin">
          <div v-for="row in preview.tree" :key="row.key" class="tree-row" :style="{ paddingLeft: 8 + row.depth * 16 + 'px' }">
            <span class="muted wbs">{{ row.wbs }}</span>
            <AppIcon :name="row.type === 'group' ? 'folder' : row.type === 'milestone' ? 'diamond' : 'gantt'" :size="12" class="muted" />
            <span class="ellipsis" :class="{ strong: row.type === 'group' }">{{ row.name }}</span>
            <span class="muted small dates">{{ value(row.start) }}<template v-if="row.end !== row.start"> — {{ value(row.end) }}</template></span>
          </div>
        </div>
      </details>

      <details v-if="preview.assignees?.length" class="sec">
        <summary class="sec-title">Исполнители</summary>
        <ul class="people">
          <li v-for="a in preview.assignees" :key="a.key">{{ a.name }} → <span :class="a.userId ? '' : 'muted'">{{ a.userName || 'без связи с сотрудником' }}</span></li>
        </ul>
      </details>
    </template>

    <template #footer>
      <button class="btn" type="button" @click="emit('close')">Отмена</button>
      <button class="btn btn-primary" type="button" :disabled="!fresh || !!busy || (target === 'project' && !title.trim())" data-testid="msp-apply" @click="apply">
        {{ busy === 'apply' ? 'Загружаем…' : target === 'update' ? 'Обновить план' : target === 'replace' ? 'Заменить' : 'Создать' }}
      </button>
    </template>
  </BaseModal>
</template>

<style scoped>
.sec { margin-bottom: 14px; }
.sec-title { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; color: var(--color-text-secondary); margin-bottom: 8px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; }
.hint { margin: 6px 0 0; font-size: 12.5px; color: var(--color-text-muted); line-height: 1.45; }
.drop { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 18px; border: 1.5px dashed var(--color-border-strong, #c9cfda); border-radius: var(--radius-md); cursor: pointer; text-align: center; color: var(--color-text-secondary); transition: border-color .15s, background .15s; }
.drop.over, .drop:hover { border-color: var(--color-primary); background: var(--color-primary-soft); }
.drop.chosen { border-style: solid; }
.drop:focus-within { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.notice { display: flex; align-items: flex-start; gap: 6px; padding: 8px 10px; border-radius: var(--radius-md); background: #fff4e0; color: #8a5300; font-size: 13px; }
.drop-main { font-size: 14px; color: var(--color-text); }
.sr-only { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { padding: 3px 10px; border-radius: 999px; background: var(--color-surface-2); font-size: 12.5px; font-weight: 600; }
.chip.new { background: #e6f6ee; color: #1f7a4c; }
.chip.changed { background: #fff4e0; color: #a8670f; }
.chip.missing { background: #fdecec; color: #c62933; }
.note { margin: 8px 0 0; font-size: 13px; }
.error { color: var(--color-danger); display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.list { max-height: 34vh; overflow: auto; border: 1px solid var(--color-border); border-radius: var(--radius-md); margin-top: 8px; }
.change-row { display: flex; flex-direction: column; gap: 2px; padding: 6px 10px; border-bottom: 1px solid #f0f2f6; }
.change-row:last-child { border-bottom: 0; }
.from { text-decoration: line-through; color: var(--color-text-muted); }
.warnings { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.5; }
.tree summary, details > summary { cursor: pointer; }
.tree-row { display: flex; align-items: center; gap: 6px; padding: 4px 10px; font-size: 13px; border-bottom: 1px solid #f4f5f8; }
.tree-row .ellipsis { flex: 1; min-width: 0; }
.tree-row .strong { font-weight: 600; }
.wbs { min-width: 34px; font-variant-numeric: tabular-nums; font-size: 12px; }
.dates { white-space: nowrap; }
.people { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.6; }
@media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
</style>
