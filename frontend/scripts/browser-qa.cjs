const { chromium } = require('playwright')
const { mkdirSync } = require('node:fs')

const root = 'C:/Users/KIIT/Documents/GDG Hackathon Project/VisionPR/test-results/browser-qa'
mkdirSync(root, { recursive: true })

async function inspect(page, path, screenshot) {
  const errors = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console: ${message.text()}`)
  })
  page.on('pageerror', (error) => errors.push(`page: ${error.message}`))
  await page.goto(`http://127.0.0.1:5173${path}`, { waitUntil: 'networkidle' })
  const result = {
    path,
    heading: await page.locator('h1').first().innerText(),
    bodyWidth: await page.evaluate(() => document.body.scrollWidth),
    viewportWidth: await page.evaluate(() => innerWidth),
    errors,
  }
  await page.screenshot({ path: `${root}/${screenshot}`, fullPage: true })
  return result
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  })
  try {
    const desktop = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } })
    const results = []
    results.push(await inspect(desktop, '/', 'landing-desktop.png'))
    results.push(await inspect(desktop, '/dashboard', 'dashboard-desktop.png'))
    results.push(await inspect(desktop, '/reviews/1', 'review-desktop.png'))
    results.push(await inspect(desktop, '/reviews/new', 'wizard-desktop.png'))
    await desktop.getByRole('button', { name: 'YouTube' }).click()
    await desktop.getByPlaceholder('https://youtu.be/...').fill('https://youtu.be/visionpr-demo')
    await desktop.getByRole('button', { name: /Continue/ }).click()
    await desktop.getByPlaceholder('Improve loading state in dashboard').fill('Improve recommendation results')
    await desktop.getByPlaceholder('https://github.com/owner/repository').fill('https://github.com/Abdeali-Badri/MOVIE-RECOMMENDER')
    await desktop.getByRole('button', { name: /Continue/ }).click()
    await desktop.getByPlaceholder('Auto-detect from repository').fill('python -m compileall .')
    await desktop.getByRole('button', { name: /Continue/ }).click()
    results.push({
      path: '/reviews/new (step 4)',
      heading: await desktop.locator('h1').first().innerText(),
      bodyWidth: await desktop.evaluate(() => document.body.scrollWidth),
      viewportWidth: await desktop.evaluate(() => innerWidth),
      errors: [],
    })
    await desktop.screenshot({ path: `${root}/wizard-launch-desktop.png`, fullPage: true })
    results.push(await inspect(mobile, '/', 'landing-mobile.png'))
    results.push(await inspect(mobile, '/dashboard', 'dashboard-mobile.png'))
    results.push(await inspect(mobile, '/reviews/1', 'review-mobile.png'))
    console.log(JSON.stringify(results, null, 2))
    if (results.some((result) => result.errors.length || result.bodyWidth > result.viewportWidth)) process.exitCode = 1
  } finally {
    await browser.close()
  }
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
