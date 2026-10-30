import { version } from '../../package.json'
import userGuidePdf from '../../docs/manuals/user-guide.pdf?url'
import userGuideDocx from '../../docs/manuals/user-guide.docx?url'
import adminGuidePdf from '../../docs/manuals/admin-guide.pdf?url'
import adminGuideDocx from '../../docs/manuals/admin-guide.docx?url'

/**
 * Руководства внутри приложения — те же docs/manuals/*.md, из которых tools/docs/build_manuals.py
 * собирает DOCX и PDF. Текст каждого руководства — отдельный файл сборки и загружается при первом
 * открытии; снимки экрана, PDF и DOCX — файлы сборки с хэшем в имени (/assets/…): скачивается
 * всегда руководство к работающей версии, а nginx кэширует их надолго.
 *
 * Руководство администратора читают только администраторы: ссылки на него скрыты от остальных,
 * маршрут /guide/admin закрыт (router/index.js). Это вопрос удобства, а не секретности: права
 * проверяет сервер, а в руководстве нет ничего, чего нет в репозитории.
 */
const images = import.meta.glob('../../docs/manuals/images/{user,admin}/*.{png,jpg,jpeg,gif,svg,webp}', {
  eager: true, query: '?url', import: 'default',
})

export const GUIDES = {
  user: {
    key: 'user',
    title: 'Руководство пользователя',
    short: 'Пользователя',
    path: '/guide',
    adminOnly: false,
    fileSlug: 'rukovodstvo-polzovatelya',
    description: 'Проекты и планы, диаграмма Ганта, связи и автосдвиг, совместная работа, история и версии, фильтры, статистика и выгрузка — с иллюстрациями.',
    pdf: userGuidePdf,
    docx: userGuideDocx,
    load: () => import('../../docs/manuals/user-guide.md?raw'),
  },
  admin: {
    key: 'admin',
    title: 'Руководство администратора',
    short: 'Администратора',
    path: '/guide/admin',
    adminOnly: true,
    fileSlug: 'rukovodstvo-administratora',
    description: 'Роли и доступы, сотрудники и направления, реестр изменений, корзина, вход через домен и производственный календарь.',
    pdf: adminGuidePdf,
    docx: adminGuideDocx,
    load: () => import('../../docs/manuals/admin-guide.md?raw'),
  },
}

/** Может ли сотрудник читать руководство (auth — хранилище stores/auth или { isAdmin }). */
export function canReadGuide(kind, auth) {
  const guide = GUIDES[kind]
  return Boolean(guide && (!guide.adminOnly || auth?.isAdmin))
}

/** Руководства, доступные сотруднику, — для ссылок и переключателя. */
export function availableGuides(auth) {
  return Object.values(GUIDES).filter((guide) => canReadGuide(guide.key, auth))
}

/**
 * PDF и DOCX руководства. Имя файла латиницей: кириллицу в имени скачиваемого файла
 * принимают не все браузеры и почтовые клиенты.
 */
export function guideDownloads(kind) {
  const guide = GUIDES[kind]
  const base = `puls-${version}-${guide.fileSlug}`
  return { pdfUrl: guide.pdf, pdfName: `${base}.pdf`, docxUrl: guide.docx, docxName: `${base}.docx` }
}

/** Адрес снимка экрана из руководства (images/user/…, images/admin/…) в сборке; null — нет такого. */
export function resolveGuideImage(src) {
  const path = String(src).replace(/^\.\//, '')
  if (!/^images\/(user|admin)\/[^/]+$/.test(path)) return null
  return images[`../../docs/manuals/${path}`] || null
}

const rendered = new Map()

/** Руководство, разобранное для чтения: { html, headings, meta, sections } (utils/markdown). */
export function loadGuide(kind) {
  const guide = GUIDES[kind]
  if (!guide) return Promise.reject(new Error(`Нет руководства «${kind}»`))
  if (!rendered.has(kind)) {
    // Разборщик Markdown загружается вместе с текстом: знаку «?» (HelpLink) на каждой странице он не нужен
    const promise = Promise.all([guide.load(), import('../utils/markdown')]).then(([source, { renderMarkdown }]) => renderMarkdown(source.default, {
      resolveImage: resolveGuideImage, headingOffset: 1, anchors: true,
    }))
    // После сбоя загрузки (нет связи) следующая попытка загружает заново
    promise.catch(() => rendered.delete(kind))
    rendered.set(kind, promise)
  }
  return rendered.get(kind)
}
