/**
 * Уменьшить изображение до maxSide пикселей по большей стороне и сжать в JPEG.
 * Фото с телефона (5–10 МБ) превращается в ~60–120 КБ — база не разрастается.
 */
export async function resizeImage(file, maxSide = 512, quality = 0.88) {
  const bitmap = await loadBitmap(file)
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height))
  const width = Math.max(1, Math.round(bitmap.width * scale))
  const height = Math.max(1, Math.round(bitmap.height * scale))
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  ctx.fillStyle = '#fff'
  ctx.fillRect(0, 0, width, height)
  ctx.drawImage(bitmap, 0, 0, width, height)
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('Не удалось обработать изображение'))), 'image/jpeg', quality)
  })
}

function loadBitmap(file) {
  if (window.createImageBitmap) return window.createImageBitmap(file)
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error('Файл не является изображением'))
    img.src = URL.createObjectURL(file)
  })
}
