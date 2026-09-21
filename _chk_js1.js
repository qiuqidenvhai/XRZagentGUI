JSON.stringify((function(){ try { return (() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const b = btns.find(x => (x.innerText||'').includes('历史记录'));
                if (b) { b.click(); return 'btn'; }
                if (typeof toggleHistory === 'function') { toggleHistory(true); return 'fn'; }
                return false; })()); } catch(e) { return {__err: String(e)}; } })())