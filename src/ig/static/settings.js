const boot = JSON.parse(document.getElementById('boot').textContent);
if (boot.android) document.body.classList.add('android-app');
// About: version, source link and (in the Android app) the update check.
document.getElementById('app-version').textContent = (boot.version || 'unknown') + (boot.build ? ` (build ${boot.build})` : '');
const repositoryLink = document.getElementById('repository-link');
repositoryLink.hidden = !boot.repository;
if (boot.repository) {
  repositoryLink.href = boot.repository;
  document.getElementById('repository-name').textContent = boot.repository.replace(/^https:\/\//, '');
}
const checkUpdates = document.getElementById('check-updates');
checkUpdates.hidden = !boot.android;
// The app's update card lives in the top page (common.js); mirror its state here.
function showUpdateState(notice) {
  // Text only: the floating card already shows the spinner and progress.
  const state = document.getElementById('update-state');
  state.hidden = !notice?.message;
  // The card floats over this page too (from the top page); leave room for it.
  document.body.classList.toggle('update-card-open', !!notice?.message);
  state.textContent = notice?.message || '';
}
{
  try {
    showUpdateState(window.top.igAppUpdateNotice);
    window.top.addEventListener('ig-app-update', event => showUpdateState(event.detail));
  } catch (error) { console.warn('Update status is unavailable here', error); }
}
let settingsData = {feeds:{},scroll_animation:boot.scroll_animation || 'smooth'};
let loadVersion = 0, scrollVersion = 0, scrollSaving = false, autoScrollSaving = false;
let scrollWrite = Promise.resolve();
// Failed changes already restore the previous control value. Wait for writes,
// but do not trap navigation after a failed, reverted settings change.
beforeLocalNavigation = async () => { await Promise.allSettled([playbackWrite, scrollWrite]); };
const autoScrollInput = document.getElementById('auto-scroll-on');
const status = document.getElementById('settings-status');
const statusHider = autoHideStatus(status);
const scrollMode = document.getElementById('scroll-mode');
async function loadSettings() {
  const version=++loadVersion;
  const currentScrollVersion=scrollVersion, currentPlaybackVersion=playbackVersion;
  try {
    const data=initialPlaybackPreferences(await settingsRequest('/settings/data'));
    if (version!==loadVersion) return;
    if (currentScrollVersion!==scrollVersion || scrollSaving) data.scroll_animation=settingsData.scroll_animation;
    if (currentPlaybackVersion!==playbackVersion || autoScrollSaving) data.auto_scroll=settingsData.auto_scroll ?? data.auto_scroll;
    settingsData=data;
    if (!autoScrollSaving) { autoScrollInput.checked=data.auto_scroll===true; autoScrollInput.disabled=false; }
    if (!scrollSaving) {
      const radio=scrollMode.querySelector(`input[value="${data.scroll_animation==='snappy'?'snappy':'smooth'}"]`);
      radio.checked=true; scrollMode.disabled=false;
    }
    const names=Object.keys(data.feeds);
    const total=new Set(Object.values(data.feeds).flat()).size;
    document.getElementById('feeds-total').textContent=`${names.length} feed${names.length===1?'':'s'} · ${total} account${total===1?'':'s'}`;
  } catch (error) {if(version===loadVersion)status.textContent=error.message;}
}
scrollMode.onchange=async event=>{
  if (event.target.name!=='scroll'||scrollSaving) return;
  const previous=settingsData.scroll_animation,mode=event.target.value;
  scrollVersion++;
  scrollSaving=true; scrollMode.disabled=true;
  try {
    scrollWrite=settingsRequest('/settings/scroll',{mode});
    const data=await scrollWrite;
    settingsData.scroll_animation=data.scroll_animation;
    try {localStorage.setItem('ig-scroll-animation',data.scroll_animation);} catch (_) {}
    status.textContent=data.scroll_animation==='smooth'?'Smooth scrolling enabled.':'Snappy scrolling enabled.';
  } catch (error) {
    scrollMode.querySelector(`input[value="${previous==='snappy'?'snappy':'smooth'}"]`).checked=true;
    status.textContent=error.message;
  } finally {scrollSaving=false;scrollMode.disabled=false;}
};
autoScrollInput.onchange=async()=>{
  const desired=autoScrollInput.checked;
  autoScrollSaving=true; autoScrollInput.disabled=true;
  try {
    const data=await savePlaybackPreferences({auto_scroll:desired});
    settingsData.auto_scroll=data.auto_scroll; autoScrollInput.checked=data.auto_scroll;
    status.textContent=data.auto_scroll?'Auto-scroll turned on.':'Auto-scroll turned off.';
  } catch (error) {autoScrollInput.checked=settingsData.auto_scroll===true;status.textContent=error.message;}
  finally {autoScrollSaving=false;autoScrollInput.disabled=false;}
};
window.addEventListener('ig-playback-migrated',event=>{
  if(!autoScrollSaving){settingsData.auto_scroll=event.detail.auto_scroll;autoScrollInput.checked=event.detail.auto_scroll;}
});
window.addEventListener('ig-feeds-changed',()=>loadSettings());
// Feeds changed on another page (common.js): show the new counts.
accountsChanged=()=>loadSettings();feedListsEdited=()=>loadSettings();
window.addEventListener('storage',event=>{
  if(event.key==='ig-playback-change'&&event.newValue&&!autoScrollSaving){
    try{const data=JSON.parse(event.newValue).changes;if(typeof data.auto_scroll==='boolean'){
      settingsData.auto_scroll=data.auto_scroll;autoScrollInput.checked=data.auto_scroll;}}catch(_){}
  }
  if(event.key==='ig-scroll-animation'&&['smooth','snappy'].includes(event.newValue)&&!scrollSaving){
    scrollVersion++;settingsData.scroll_animation=event.newValue;scrollMode.querySelector(`input[value="${event.newValue}"]`).checked=true;
  }
});
// Back navigation can restore this page without fresh server data.
window.addEventListener('pageshow',event=>{
  if(event.persisted)playbackWrite.catch(()=>{}).then(()=>loadSettings());
});
loadSettings();
