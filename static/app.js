function el(q){return document.querySelector(q)}
function elAll(q){return document.querySelectorAll(q)}

function addCredRow(username='', password=''){
  const container = el('#creds')
  const row = document.createElement('div')
  row.className = 'cred-row'
  row.innerHTML = `<input name="username" placeholder="username (email)" value="${username}" /> <input name="password" placeholder="password" value="${password}" /> <button class="start-cred" type="button">Start</button> <button class="remove" type="button">Remove</button> <span class="cred-status"></span>`
  container.appendChild(row)
}

el('#add-cred').addEventListener('click', ()=> addCredRow())

el('#creds').addEventListener('click', (e)=>{
  if(e.target.classList.contains('remove')){
    const row = e.target.closest('.cred-row')
    row.remove()
  }
  if(e.target.classList.contains('start-cred')){
    const row = e.target.closest('.cred-row')
    const username = row.querySelector('input[name="username"]').value
    const password = row.querySelector('input[name="password"]').value
    // disable start button to prevent duplicate starts
    e.target.disabled = true
    row.querySelector('.cred-status').textContent = 'starting...'
    startSingleCredential({username, password}).then(sid=>{
      row.querySelector('.cred-status').textContent = 'started: ' + sid
    }).catch(err=>{
      row.querySelector('.cred-status').textContent = 'error'
      e.target.disabled = false
      alert('Start error: '+err.message)
    })
  }
})

el('#main-form').addEventListener('submit', async (ev)=>{
  ev.preventDefault()
  const rows = Array.from(elAll('#creds .cred-row'))
  const creds = rows.map(r=>({
    username: r.querySelector('input[name="username"]').value,
    password: r.querySelector('input[name="password"]').value
  }))

  const fd = new FormData()
  fd.append('credentials', JSON.stringify(creds))
  const f = el('#excel').files[0]
  if(f) fd.append('excel', f)

  el('#log').textContent = 'Starting session...'
  const res = await fetch('/start', {method:'POST', body: fd})
  const data = await res.json()
  if(data.session_id){
    // track multiple sessions
    window._sessions = window._sessions || []
    window._sessions.push(data.session_id)
    el('#session').textContent = 'Sessions: ' + window._sessions.join(', ')
    el('#controls').style.display = 'block'
    el('#log').textContent = data.message
    // show schedule area and start polling status
    el('#schedule').style.display = 'block'
    startStatusPolling()
  } else {
    el('#log').textContent = JSON.stringify(data, null, 2)
  }
})

el('#continue').addEventListener('click', async ()=>{
  const ids = window._sessions || []
  if(ids.length === 0) return alert('No sessions')
  el('#log').textContent = 'Sending continue...'
  // call continue for each top-level session
  const results = []
  for(const id of ids){
    const res = await fetch('/continue/' + id, {method:'POST'})
    results.push(await res.json())
  }
  el('#log').textContent = JSON.stringify(results, null, 2)
})

el('#check').addEventListener('click', async ()=>{
  const ids = window._sessions || []
  if(ids.length === 0) return alert('No sessions')
  const all = {}
  for(const id of ids){
    const res = await fetch('/status/' + id)
    all[id] = await res.json()
  }
  el('#log').textContent = JSON.stringify(all, null, 2)
})

let _poller = null
function startStatusPolling(interval = 5000){
  if(_poller) return
  _poller = setInterval(async ()=>{
    const ids = window._sessions || []
    if(ids.length === 0) return
    try{
      const results = {}
      for(const id of ids){
        const res = await fetch('/status/' + id)
        results[id] = await res.json()
      }
      renderStatuses(results)
    }catch(e){
      el('#log').textContent = 'Status poll error: '+e.message
    }
  }, interval)
}

function stopStatusPolling(){
  if(_poller){ clearInterval(_poller); _poller = null }
}

function renderStatuses(all){
  if(!all) return
  const sessions = Object.keys(all)
  el('#log').textContent = JSON.stringify(sessions.map(s=>({id:s, status: all[s].status})), null, 2)
  const list = el('#schedule-list')
  list.innerHTML = ''
  for(const sid of sessions){
    const session = all[sid]
    const header = document.createElement('div')
    header.className = 'session-header'
    header.innerHTML = `<h4>Session ${sid}</h4>`
    list.appendChild(header)
    const children = session.children || {}
    for(const cid of Object.keys(children)){
      const c = children[cid]
      const item = document.createElement('div')
      item.className = 'sch-item'
      const next = c.next_run ? new Date(c.next_run).toLocaleString() : '—'
      const accepted = (c.result && c.result.accepted) ? c.result.accepted : 0
      item.innerHTML = `<strong>${cid}</strong> — <em>${c.status}</em> — next: ${next} — accepted: ${accepted}`
      list.appendChild(item)
    }
  }
}

async function startSingleCredential(cred){
  const fd = new FormData()
  fd.append('credentials', JSON.stringify([cred]))
  const res = await fetch('/start', {method:'POST', body: fd})
  const data = await res.json()
  if(data.session_id){
    window._sessions = window._sessions || []
    window._sessions.push(data.session_id)
    el('#session').textContent = 'Sessions: ' + window._sessions.join(', ')
    el('#controls').style.display = 'block'
    el('#schedule').style.display = 'block'
    startStatusPolling()
    return data.session_id
  }
  throw new Error(JSON.stringify(data))
}

// Add initial one row
addCredRow()
