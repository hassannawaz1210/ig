document.getElementById('retry-page')?.addEventListener('click',event => {
  event.preventDefault();
  event.currentTarget.textContent='Trying again…';
  event.currentTarget.setAttribute('aria-disabled','true');
  location.reload();
});
