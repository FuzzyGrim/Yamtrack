import{a as h}from"./chunk-7NBN4MO5.js";import"./chunk-IX2UCJEJ.js";import{a as g}from"./chunk-IXINCD7J.js";import"./chunk-WPWWTNKH.js";import"./chunk-M7AW2FPM.js";import"./chunk-PUPAV2RR.js";import"./chunk-LGPPW2RT.js";import"./chunk-5ZL4T72Q.js";import"./chunk-RN2L2TRT.js";import{c as u}from"./chunk-DH5EGAFP.js";import{d as p}from"./chunk-ENJOBGBJ.js";import"./chunk-73KGKOME.js";import{a as f,c as l}from"./chunk-TFHLQ7R6.js";import{b as i}from"./chunk-SQE76S5B.js";var P=i(f());var s=i(f());var n=i(l()),m=document.createElement("canvas");m.width=m.height=1;var d=m.getContext("2d");function y(t,e){if(!t)return e;if(/^(?:#|rgba?\()/i.test(t.trim()))return t.trim();if(!d)return e;d.clearRect(0,0,1,1),d.fillStyle=e,d.fillStyle=t,d.fillRect(0,0,1,1);let[o,r,a,c]=d.getImageData(0,0,1,1).data;return`rgba(${o}, ${r}, ${a}, ${c/255})`}function w(){let t=getComputedStyle(document.documentElement),e=(r,a)=>t.getPropertyValue(r).trim()||a,o=(r,a)=>y(e(r,a),a);return{card:o("--color-surface","#27272a"),text:o("--color-text","#fafafa"),textSecondary:o("--color-text-secondary","#d4d4d8"),textMuted:o("--color-text-muted","#a1a1aa"),border:o("--color-surface-border","#3f3f46"),link:o("--color-link","#818cf8"),star:o("--color-warning","#facc15"),chipBackground:"rgba(24, 24, 27, 0.92)",chipText:"#ffffff",placeholder:o("--color-surface-strong","#3f3f46"),progressTrack:"rgba(24, 24, 27, 0.6)",progressFill:o("--color-accent","#6366f1"),progressPaused:"rgba(99, 102, 241, 0.4)",rating:{lit:o("--color-warning","#facc15"),unlit:o("--color-surface-muted","#52525b"),muted:o("--color-text-muted","#a1a1aa"),focusStyle:{}}}}var v=`
  [data-detail-related-root] [data-detail-related-active="true"] {
    border-radius: 10px;
    outline: 2px solid #4338ca;
    outline-offset: 0;
  }
  [data-detail-related-root] .media-card-year {
    max-height: 0;
    margin-top: 0;
    overflow: hidden;
    opacity: 0;
    color: rgba(255, 255, 255, 0.82);
    font-weight: 400;
    text-shadow: 0 1px 2px rgba(0, 0, 0, 0.9);
    transition: max-height 0.2s ease, margin-top 0.2s ease, opacity 0.2s ease;
  }
  [data-detail-related-root] .media-card:hover .media-card-year {
    max-height: 2lh;
    margin-top: 0.25rem;
    opacity: 1;
  }
  [data-detail-related-root] .media-card .media-card-visual {
    aspect-ratio: 2 / 3;
  }
  [data-detail-related-root] .media-card .media-card-poster {
    height: auto !important;
    aspect-ratio: auto !important;
  }
  [data-detail-related-root] .media-card .media-card-body {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 10;
  }
  [data-detail-related-root] .media-card-title {
    display: block;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    -webkit-line-clamp: unset;
    line-clamp: unset;
  }
  #detail-secondary-content [data-detail-section="recommendations"] ul {
    margin-top: 0;
    margin-bottom: 0;
  }
  #detail-secondary-content [data-detail-section="recommendations"] h3 {
    line-height: 28px;
  }
`;function k(t,e){if(e){if(e.kind==="link"){e.href&&window.location.assign(e.href);return}if(e.kind==="post"){window.htmx?.ajax("POST",e.url,{target:e.target,swap:e.swap,values:e.vals,headers:{"X-CSRFToken":t?.csrfToken||t?.actions?.csrfToken||""}});return}g(e)}}function R(t,e){let o=t?.rating?.rateUrl;o&&window.htmx?.ajax("POST",o,{swap:"none",headers:{"X-CSRFToken":t.csrfToken||""},values:{score:e??t.rating.value,toggle:!0}})}function S({props:t}){let[e,o]=(0,s.useState)(w);return(0,s.useEffect)(()=>{let r=()=>o(w());r();let a=new MutationObserver(r);a.observe(document.documentElement,{attributes:!0,attributeFilter:["class","style"]});let c=window.matchMedia("(prefers-color-scheme: dark)");return c.addEventListener("change",r),()=>{a.disconnect(),c.removeEventListener("change",r)}},[]),(0,n.jsxs)(n.Fragment,{children:[(0,n.jsx)("style",{children:v}),(0,n.jsx)(h,{model:t.model,labels:t.labels,palette:e,web:!0,onOpen:r=>{r?.href&&window.location.assign(r.href)},onOpenLink:r=>{r&&window.location.assign(r)},onOpenExternal:r=>{r&&window.open(r,"_blank","noopener,noreferrer")},onAction:k,onRate:R,renderActionIcon:r=>(0,n.jsx)(u,{spec:r.icon})})]})}function x({host:t,props:e}){return(0,n.jsx)(S,{host:t,props:e})}var b=i(l());function M(t,e){return p(t,(0,b.jsx)(x,{host:t,props:e}))}export{M as mountDetailRelated};
