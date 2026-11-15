// A minimal in-memory DOM: enough for react-dom/client to mount, update and unmount.
class Node {
  constructor(nodeType, name, doc) { this.nodeType = nodeType; this.nodeName = name; this.ownerDocument = doc; this.childNodes = []; this.parentNode = null; }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes.at(-1) || null; }
  get nextSibling() { const s = this.parentNode?.childNodes; return s ? s[s.indexOf(this) + 1] || null : null; }
  appendChild(c) { c.parentNode?.removeChild(c); this.childNodes.push(c); c.parentNode = this; return c; }
  insertBefore(c, ref) { if (!ref) return this.appendChild(c); c.parentNode?.removeChild(c); this.childNodes.splice(this.childNodes.indexOf(ref), 0, c); c.parentNode = this; return c; }
  removeChild(c) { this.childNodes.splice(this.childNodes.indexOf(c), 1); c.parentNode = null; return c; }
  addEventListener() {} removeEventListener() {}
  get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map(c => c.textContent).join(''); }
  set textContent(v) { this.childNodes = []; if (v !== '' && v != null) this.appendChild(this.ownerDocument.createTextNode(String(v))); }
}
class Element extends Node {
  constructor(name, doc) { super(1, name.toUpperCase(), doc); this.tagName = this.nodeName; this.attributes = {}; this.style = {}; this.namespaceURI = 'http://www.w3.org/1999/xhtml'; }
  setAttribute(k, v) { this.attributes[k] = String(v); } getAttribute(k) { return this.attributes[k] ?? null; }
  removeAttribute(k) { delete this.attributes[k]; } hasAttribute(k) { return k in this.attributes; }
}
class Text extends Node { constructor(v, doc) { super(3, '#text', doc); this.nodeValue = v; } get data() { return this.nodeValue; } set data(v) { this.nodeValue = v; } }
export function installFakeDom() {
  const doc = new Node(9, '#document', null);
  doc.ownerDocument = null;
  doc.createElement = name => new Element(name, doc);
  doc.createElementNS = (_ns, name) => new Element(name, doc);
  doc.createTextNode = v => new Text(v, doc);
  doc.documentElement = doc.createElement('html');
  doc.body = doc.createElement('body');
  doc.activeElement = doc.body;
  doc.defaultView = globalThis;
  globalThis.document = doc;
  globalThis.window = globalThis;
  globalThis.HTMLElement = Element;
  globalThis.HTMLIFrameElement = class {};
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  return doc;
}
