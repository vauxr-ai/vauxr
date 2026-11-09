/** Read-only browser design checks. Details contain selectors/counts, never page text. */
export async function auditPage(page, tokens, label = 'page') {
  const results = [];
  const add = (rule, status, detail, fixable = false) => results.push({ rule, status, detail: `${label}: ${detail}`, fixable });
  // A native dialog can report visible while its entrance still has opacity zero.
  await page.evaluate(async () => {
    const entrances = [...document.querySelectorAll('dialog[open],[aria-modal="true"]')]
      .flatMap(el => el.getAnimations({subtree:true}))
      .filter(a => a instanceof CSSAnimation && Number(a.effect?.getComputedTiming().endTime) <= 1000);
    await Promise.all(entrances.map(a => a.finished.catch(() => {})));
  });
  const capture = (tokens) => {
    const visible = (el) => {
      if (!(el instanceof Element) || el.closest('[hidden], [inert], .sr-only, .visually-hidden')) return false;
      for (let p = el; p; p = p.parentElement) {
        const s = getComputedStyle(p);
        if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
        if (p.matches('details:not([open])') && el !== p && !p.querySelector(':scope > summary')?.contains(el)) return false;
      }
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 1 && r.height > 1 && !(s.clip !== 'auto' || s.clipPath === 'inset(50%)');
    };
    const elements = [...document.querySelectorAll('body *')].filter(visible);
    const tag = (el) => el.tagName.toLowerCase();
    const selector = el => {
      const parts=[];
      for(let p=el;p && p!==document.body;p=p.parentElement) {
        const siblings=[...(p.parentElement?.children || [])].filter(s=>s.tagName===p.tagName);
        parts.unshift(`${p.tagName.toLowerCase()}${[...p.classList].slice(0,3).map(c=>'.'+CSS.escape(c)).join('')}:nth-of-type(${siblings.indexOf(p)+1})`);
        if(parts.length===4) break;
      }
      return parts.join(' > ');
    };
    const parse = (value) => {
      const rgb = value.match(/^rgba?\(\s*([\d.]+)[, ]+([\d.]+)[, ]+([\d.]+)(?:\s*[,/]\s*([\d.]+))?\s*\)$/);
      if(rgb) return [Number(rgb[1]),Number(rgb[2]),Number(rgb[3]),rgb[4]===undefined?1:Number(rgb[4])];
      const probe = document.createElement('span');
      probe.style.color = value;
      if (!probe.style.color) return null;
      // Resolve absolute CSS colors without inserting a node into the application.
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx) return null;
      ctx.fillStyle = value;
      ctx.fillRect(0, 0, 1, 1);
      return [...ctx.getImageData(0, 0, 1, 1).data].map((n, i) => i === 3 ? n / 255 : n);
    };
    const palette = [];
    const collect = (obj) => {
      for (const [key, value] of Object.entries(obj || {})) {
        if (value && typeof value === 'object') collect(value);
        else if (typeof value === 'string' && (/^#|^rgba?\(|^hsla?\(|^oklch\(|^color\(/.test(value))) {
          const c = parse(value); if (c) palette.push(c);
        }
      }
    };
    collect(tokens);
    const same = (a, b) => a.slice(0, 3).every((n, i) => Math.abs(n - b[i]) <= 1);
    const colorCounts = { opaque: 0, derivedAlpha: 0, unmapped: 0, unsupported: 0 };
    const unmapped = new Set();
    const colorEvidence=[];
    const contrastEvidence=[];
    const scrollEvidence=[];
    for (const el of elements) {
      const s = getComputedStyle(el);
      for (const prop of ['color', 'backgroundColor', 'borderTopColor', 'borderRightColor', 'borderBottomColor', 'borderLeftColor', 'outlineColor', 'fill', 'stroke']) {
        if (prop.startsWith('border')) {
          const side = prop.slice(6, -5);
          if (parseFloat(s[`border${side}Width`]) === 0 || s[`border${side}Style`] === 'none') continue;
        }
        if (prop === 'outlineColor' && (s.outlineStyle === 'none' || parseFloat(s.outlineWidth) === 0)) continue;
        // SVG containers inherit paint but do not draw it; inspect their actual shapes.
        if ((prop === 'fill' || prop === 'stroke') && !el.matches('path,rect,circle,ellipse,line,polyline,polygon,text,tspan,use')) continue;
        if (prop === 'fill' && el.matches('line')) continue;
        if (s[prop] === 'none') continue;
        const c = parse(s[prop]);
        if (!c) { colorCounts.unsupported++; continue; }
        if (c[3] === 0) continue;
        if (palette.some(p => same(c, p))) colorCounts[c[3] < 0.999 ? 'derivedAlpha' : 'opaque']++;
        else { colorCounts.unmapped++; unmapped.add(s[prop]); colorEvidence.push({selector:selector(el),property:prop,color:s[prop]}); }
      }
      const stops = s.backgroundImage.match(/#[a-f\d]{3,8}\b|rgba?\([^)]+\)/gi) || [];
      for (const stop of stops) { const c = parse(stop); if(c && c[3]>0 && !palette.some(p=>same(c,p))) {colorCounts.unmapped++;unmapped.add(stop);colorEvidence.push({selector:selector(el),property:"backgroundImage",color:stop});} }
    }
    const over = elements.filter(el => {
      if (el.matches('input,select,textarea,option')) return false;
      const r = el.getBoundingClientRect();
      return r.left < -1 || r.right > innerWidth + 1;
    });
    const modals = elements.filter(el => el.matches('[role="dialog"],dialog[open],[aria-modal="true"]'));
    let modalBounds = 0, modalScroll = 0, modalClip = 0;
    for (const modal of modals) {
      const r = modal.getBoundingClientRect();
      if (r.left < -1 || r.top < -1 || r.right > innerWidth + 1 || r.bottom > innerHeight + 1) modalBounds++;
      for (const el of [modal, ...modal.querySelectorAll('*')].filter(visible)) {
        if (el.matches('input,textarea,select,option,svg,svg *')) continue;
        const s = getComputedStyle(el);
        const namedEllipsis = el.matches('.pipeline-agent-name,.pipeline-agent .pipeline-entity > strong,.agent-voice-modal .modal-body > h3')
          && el.getAttribute('title') === el.textContent && !!el.textContent?.trim()
          && s.overflow === 'hidden' && (s.textOverflow === 'ellipsis' || s.webkitLineClamp === '2');
        if (namedEllipsis) continue;
        for (const [axis, delta] of [['X', el.scrollWidth - el.clientWidth], ['Y', el.scrollHeight - el.clientHeight]]) {
          // Inline elements have no client box; native controls have intentional intrinsic content.
          if (!el.clientWidth || !el.clientHeight || delta <= 2) continue;
          const overflow = s[`overflow${axis}`];
          if (/auto|scroll/.test(overflow)) modalScroll++;
          if (/hidden|clip/.test(overflow)) modalClip++;
        }
      }
    }
    const composite = (fg, bg) => [0, 1, 2].map(i => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1);
    const luminance = c => c.slice(0, 3).map(n => { n /= 255; return n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4; }).reduce((v, n, i) => v + n * [.2126, .7152, .0722][i], 0);
    let contrastChecked = 0, contrastFailed = 0, contrastUnknown = 0;
    for (const el of elements) {
      const hasText = [...el.childNodes].some(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
      if (!hasText || el.matches('script,style,option')) continue;
      let bg = [255, 255, 255, 1], unknown = false;
      const chain = []; for (let p = el; p; p = p.parentElement) chain.unshift(p);
      for (const p of chain) {
        const s = getComputedStyle(p);
        if (s.backgroundImage !== 'none' || Number(s.opacity) < 1 || s.mixBlendMode !== 'normal' || s.filter !== 'none' || s.backdropFilter && s.backdropFilter !== 'none') unknown = true;
        const c = parse(s.backgroundColor);
        if (!c) unknown = true; else bg = composite(c, bg);
        if (p.matches('canvas,video,svg') || s.textShadow !== 'none') unknown = true;
        if (['::before', '::after'].some(pseudo => { const ps = getComputedStyle(p, pseudo); return ps.content !== 'none' && ps.content !== 'normal' && (ps.backgroundImage !== 'none' || ps.backgroundColor !== 'rgba(0, 0, 0, 0)'); })) unknown = true;
      }
      const s = getComputedStyle(el), fg = parse(s.color);
      if (unknown || !fg || el.matches(':disabled') || el.closest('[aria-disabled="true"]')) { contrastUnknown++; continue; }
      const ink = composite(fg, bg), l1 = luminance(ink), l2 = luminance(bg);
      const ratio = (Math.max(l1, l2) + .05) / (Math.min(l1, l2) + .05);
      const large = parseFloat(s.fontSize) >= 24 || (parseFloat(s.fontSize) >= 18.6667 && parseFloat(s.fontWeight) >= 700);
      contrastChecked++;
      if (ratio + .01 < (large ? 3 : 4.5)) {
        contrastFailed++;
        contrastEvidence.push({selector:selector(el),foreground:s.color,background:bg,ratio,required:large?3:4.5,fontSize:s.fontSize,fontWeight:s.fontWeight});
      }
    }
    return {
      horizontalScroll: elements.filter(el => {
        if (el.closest('[role="dialog"],dialog,[aria-modal="true"]') || el.matches('input,textarea,select,option,svg,svg *')) return false;
        const style = getComputedStyle(el);
        // Long management pages may scroll vertically; modal interiors are checked above.
        const scroll = el.clientHeight > 0 && el.clientWidth > 0 && /auto|scroll/.test(style.overflowX) && el.scrollWidth > el.clientWidth + 2;
        if(scroll) scrollEvidence.push({selector:selector(el),width:el.clientWidth,height:el.clientHeight,scrollWidth:el.scrollWidth,scrollHeight:el.scrollHeight,overflowX:style.overflowX,overflowY:style.overflowY,backgroundUnderModal:!!document.querySelector('dialog:modal')});
        return scroll;
      }).length,
      overflow: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0) > innerWidth + 1,
      over: over.length, modalCount: modals.length, modalBounds, modalScroll, modalClip,
      colorCounts, unmappedCount: unmapped.size, unmappedValues: [...unmapped].slice(0,12), paletteSize: palette.length,
      contrastChecked, contrastFailed, contrastUnknown, colorEvidence, contrastEvidence, scrollEvidence,
      finiteTransitions: document.getAnimations().some(a => a instanceof CSSTransition && Number(a.effect?.getComputedTiming().endTime) <= 1000),
      loading: elements.filter(el => [...el.childNodes].some(n => n.nodeType === Node.TEXT_NODE && /\bLoading\s*(?:…|\.\.\.)/i.test(n.textContent))).length,
      headings: elements.filter(el => el.matches('main h1, [role="main"] h1')).length,
      hoverCount: elements.filter(el => el.matches('button,a[href],summary') && !el.matches(':disabled,[aria-disabled="true"]')).length,
      tags: [...new Set(elements.map(tag))].filter(t => ['iframe', 'canvas'].includes(t)).length,
    };
  };
  const snapshot = await page.evaluate(capture, tokens || {});
  if (snapshot.colorCounts.unmapped && snapshot.finiteTransitions) {
    // Token endpoints are the contract, not the temporary interpolated RGB values.
    // Contrast keeps its original instantaneous sample; only paint-token evidence is resampled.
    await page.evaluate(async () => {
      await Promise.all(document.getAnimations().filter(a => a instanceof CSSTransition &&
        Number(a.effect?.getComputedTiming().endTime) <= 1000).map(a => a.finished.catch(() => {})));
    });
    const settled = await page.evaluate(capture, tokens || {});
    for (const key of ['colorCounts','unmappedCount','unmappedValues','paletteSize','colorEvidence']) snapshot[key] = settled[key];
  }
  add('overflow', snapshot.overflow || snapshot.over || snapshot.horizontalScroll ? 'FAIL' : 'PASS', `page overflow=${snapshot.overflow}; ${snapshot.over} visible boxes outside horizontal viewport; ${snapshot.horizontalScroll} horizontal page scrolling containers`, true);
  add('overflow', snapshot.modalBounds || snapshot.modalScroll || snapshot.modalClip ? 'FAIL' : 'PASS', `${snapshot.modalCount} visible dialogs; ${snapshot.modalBounds} outside viewport; ${snapshot.modalScroll} internal scroll axes; ${snapshot.modalClip} clipped axes`, true);
  results[0].evidence = snapshot.scrollEvidence;
  const colors = snapshot.colorCounts;
  add('token-colors', colors.unmapped ? 'FAIL' : !snapshot.paletteSize || colors.unsupported ? 'WARN' : 'PASS', `${snapshot.paletteSize} token colors; ${colors.opaque} opaque matches; ${colors.derivedAlpha} alpha-derived matches; ${snapshot.unmappedCount} unmapped colors (${colors.unmapped} uses); ${colors.unsupported} unsupported uses; unmapped=${snapshot.unmappedValues.join(", ")}`, true);
  results.at(-1).evidence = snapshot.colorEvidence;
  add('contrast', snapshot.contrastFailed ? 'FAIL' : snapshot.contrastChecked ? 'PASS' : 'WARN', `${snapshot.contrastChecked} solid-background text checks; ${snapshot.contrastFailed} below WCAG AA`, true);
  results.at(-1).evidence = snapshot.contrastEvidence;
  if (snapshot.contrastUnknown) add('contrast', 'WARN', `${snapshot.contrastUnknown} text elements require manual contrast review (images, gradients, effects, or disabled controls)`);
  add('loading-text', snapshot.loading ? 'FAIL' : 'PASS', `${snapshot.loading} visible Loading ellipsis placeholders`, true);
  add('page-headings', snapshot.headings > 1 ? 'FAIL' : snapshot.headings ? 'PASS' : 'WARN', `${snapshot.headings} visible h1 headings`, true);

  // Hover only: never click, focus, navigate, or read control values/text.
  const controls = page.locator('body *');
  const count = await controls.count();
  let hovered = 0, moved = 0, skipped = 0;
  const transform = el => {
    const s = getComputedStyle(el);
    return [null,'::before','::after'].map(p => {const s=getComputedStyle(el,p);return [s.transform,s.translate,s.scale,s.rotate].join('|')}).join(';');
  };
  const hoverSelectors = await page.evaluate(() => {
    const selectors = [];
    const walk = rules => { for (const rule of rules) {
      if (rule.selectorText?.includes(':hover') && ['transform','translate','scale','rotate'].some(p => rule.style?.getPropertyValue(p))) selectors.push(rule.selectorText.replaceAll(':hover','').replace(/::(?:before|after)/g,''));
      if (rule.cssRules) walk(rule.cssRules);
    }};
    for(const sheet of document.styleSheets) { try { walk(sheet.cssRules); } catch {} }
    return selectors;
  });
  try {
    const candidates = await controls.evaluateAll((elements, selectors) => elements.flatMap((el,i) => {
      const r=el.getBoundingClientRect(),s=getComputedStyle(el);
      const matches=selectors.some(selector=>{try{return el.matches(selector)}catch{return false}});
      return matches && !el.matches(':disabled,[aria-disabled="true"]') && !el.closest('[inert],.sr-only,.visually-hidden') && s.visibility==='visible' && Number(s.opacity)>0 && r.width>1 && r.height>1 && r.top>=0 && r.left>=0 && r.bottom<=innerHeight && r.right<=innerWidth ? [i] : [];
    }),hoverSelectors);
    for (const i of candidates) {
      const control = controls.nth(i);
      const eligible = await control.isVisible();
      if (!eligible) { skipped++; continue; }
      await page.mouse.move(0, 0);
      await page.waitForTimeout(35);
      const before = await control.evaluate(transform);
      try { await control.hover({ timeout: 700, force: false }); } catch { skipped++; continue; }
      await page.waitForTimeout(60);
      const after = await control.evaluate(transform);
      hovered++;
      if (before !== after) moved++;
    }
  } finally { await page.mouse.move(0, 0).catch(() => {}); }
  add('hover-motion', moved ? 'FAIL' : 'PASS', `${hovered} transform-rule candidate controls hovered; ${moved} changed transform/translate/scale/rotate`, true);
  if (skipped) add('hover-coverage', 'WARN', `${skipped} obstructed transform-rule candidates not hovered`);

  const previousMotion = await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches);
  try {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.waitForTimeout(50);
    const motion = await page.evaluate(() => {
      let durations = 0;
      const seconds = v => v.split(',').some(n => parseFloat(n) > (n.trim().endsWith('ms') ? 1 : .001));
      for (const el of document.querySelectorAll('body *')) {
        const r = el.getBoundingClientRect();
        if (r.width <= 1 || r.height <= 1 || el.closest('[hidden],[inert],.sr-only,.visually-hidden')) continue;
        for (const pseudo of [null, '::before', '::after', '::details-content', '::backdrop']) {
          const s = getComputedStyle(el, pseudo);
          if (s.display === 'none' || s.visibility === 'hidden') continue;
          if (pseudo && pseudo !== '::details-content' && pseudo !== '::backdrop' && (s.content === 'none' || s.content === 'normal')) continue;
          if ((s.animationName !== 'none' && s.animationPlayState !== 'paused' && seconds(s.animationDuration)) || (s.transitionProperty !== 'none' && seconds(s.transitionDuration))) durations++;
        }
      }
      const running = document.getAnimations().filter(a => a.playState === 'running' && Number(a.effect?.getComputedTiming().duration) > 1).length;
      return { durations, running };
    });
    add('reduced-motion', motion.durations || motion.running ? 'FAIL' : 'PASS', `${motion.durations} nonzero animation/transition styles; ${motion.running} running animations under reduced motion`, true);
  } finally {
    // Restore the effective preference; Playwright has no public getter for media overrides.
    await page.emulateMedia({ reducedMotion: previousMotion ? 'reduce' : 'no-preference' });
  }
  add('audit-coverage', 'WARN', `Current DOM/viewport only; token paint checks cover solid text/background/borders/outline/SVG and RGB gradient stops, not shadows or embedded artwork; unopened states, press/focus motion, shadow roots, image contrast, overlapping backgrounds and ${snapshot.tags} embedded/canvas types need review. Media restored to prior effective preference (override provenance unavailable).`);
  return results;
}
