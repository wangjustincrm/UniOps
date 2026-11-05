import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { Markdown, parseBlocks } from './markdown'

describe('Assistant Markdown (ISS-0012)', () => {
  it('renders bold instead of showing the asterisks', () => {
    const { container } = render(<Markdown text="现在的状态是 **Issued**" />)
    expect(container.querySelector('strong')?.textContent).toBe('Issued')
    expect(container.textContent).not.toContain('**')
  })

  it('renders a pipe table as a table', () => {
    const md = '| 协议号 | 标题 | 到期日期 |\n|------|------|------|\n| AGR-202608-0009 | Indeed IO | 2026-10-27 |'
    const { container } = render(<Markdown text={md} />)
    expect([...container.querySelectorAll('th')].map((t) => t.textContent)).toEqual(['协议号', '标题', '到期日期'])
    expect([...container.querySelectorAll('td')].map((t) => t.textContent)).toEqual(['AGR-202608-0009', 'Indeed IO', '2026-10-27'])
    expect(container.textContent).not.toContain('|')
    expect(container.textContent).not.toContain('---')
  })

  it('renders lists and keeps line breaks in paragraphs', () => {
    const { container } = render(<Markdown text={'第一行\n第二行\n\n- 甲\n- **乙**\n\n1. one\n2. two'} />)
    expect(container.querySelectorAll('br')).toHaveLength(1)
    expect([...container.querySelectorAll('ul li')].map((l) => l.textContent)).toEqual(['甲', '乙'])
    expect(container.querySelector('ul strong')?.textContent).toBe('乙')
    expect([...container.querySelectorAll('ol li')].map((l) => l.textContent)).toEqual(['one', 'two'])
  })

  it('never interprets markup in the reply text', () => {
    const { container } = render(<Markdown text={'<img src=x onerror=alert(1)> **<b>vendor</b>**'} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
    expect(container.querySelector('strong')?.textContent).toBe('<b>vendor</b>')
  })

  it('leaves a lone pipe line as text, not a table', () => {
    expect(parseBlocks('A | B is just text').map((b) => b.kind)).toEqual(['p'])
  })

  it('does not treat a multiplication-like asterisk as italic', () => {
    const { container } = render(<Markdown text="qty * price = total" />)
    expect(container.querySelector('em')).toBeNull()
    expect(container.textContent).toBe('qty * price = total')
  })
})
