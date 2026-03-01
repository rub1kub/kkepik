(function(){
  function openDisclaimer(){
    var m = document.getElementById('cgDisclaimer');
    if (!m) return;
    m.style.display = 'block';
    document.body.style.overflow = 'hidden';
    var c = m.querySelector('.cg-disclaimer-content');
    if (c) requestAnimationFrame(function(){ c.classList.add('is-appearing'); });
  }
  function openSeasonInfoModal(){
    var infoModal = document.getElementById('seasonInfoModal');
    if (!infoModal) return;
    infoModal.style.display = 'block';
    requestAnimationFrame(function(){ var c = infoModal.querySelector('.info-content'); if (c) c.classList.add('is-appearing'); });
  }
  function thanosSnapDismiss(){
    var m = document.getElementById('cgDisclaimer');
    if (!m) return;
    var c = m.querySelector('.cg-disclaimer-content');
    if (!c) { m.style.display='none'; document.body.style.overflow=''; return; }
    var layer = document.createElement('div');
    layer.className = 'cg-snap-layer';
    m.appendChild(layer);
    var rect = c.getBoundingClientRect();
    var particles = 160;
    var colors = ['#d4af37','#e6cc96','#977143','#c5a26b','#473219'];
    for (var i=0;i<particles;i++){
      var p = document.createElement('div');
      p.className = 'cg-snap-particle';
      var x = rect.left + Math.random()*rect.width;
      var y = rect.top + Math.random()*rect.height;
      var size = 5 + Math.random()*7;
      var dx = (Math.random()*2-1) * (rect.width*1.2);
      var dy = (-Math.random()*0.9) * (rect.height*1.4) - 80;
      var rot = (Math.random()*1080-540)+'deg';
      p.style.left = x+'px'; p.style.top = y+'px';
      p.style.width = size+'px'; p.style.height = size+'px';
      p.style.background = colors[i % colors.length];
      p.style.setProperty('--dx', dx+'px');
      p.style.setProperty('--dy', dy+'px');
      p.style.setProperty('--rot', rot);
      p.style.animation = 'cgDust '+(850+Math.random()*900)+'ms ease-out forwards';
      p.style.animationDelay = (Math.random()*220)+'ms';
      layer.appendChild(p);
    }
    c.classList.add('snap-fade');
    setTimeout(function(){
      m.style.display='none';
      document.body.style.overflow='';
      if (layer && layer.parentNode) layer.parentNode.removeChild(layer);
      // auto-open season info modal after first close
      if (!localStorage.getItem('cg_disclaimer_ack')) {
        openSeasonInfoModal();
      } else {
        // If we are in the click handler setting it just now, still open
        openSeasonInfoModal();
      }
    }, 1500);
  }
  document.addEventListener('DOMContentLoaded', function(){
    try {
      if (!localStorage.getItem('cg_disclaimer_ack')) {
        openDisclaimer();
      }
      var ok = document.getElementById('cgDisclaimerOk');
      if (ok) ok.addEventListener('click', function(){
        localStorage.setItem('cg_disclaimer_ack','1');
        try { window.Telegram && Telegram.WebApp && Telegram.WebApp.HapticFeedback && Telegram.WebApp.HapticFeedback.impactOccurred('light'); } catch(_) {}
        thanosSnapDismiss();
      });
    } catch(_) {}
  });
})();
