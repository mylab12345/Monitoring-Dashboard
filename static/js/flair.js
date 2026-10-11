/* ================================================================
   Visual flair — cursor-following spotlight for cards.
   Presentational only: it writes two CSS custom properties (--mx/--my)
   on the card under the pointer; styling lives in dashboard.css §10.
   ================================================================ */
(function(){
  'use strict';
  var pending = null, lastCard = null, lastX = 0, lastY = 0;

  function paint(){
    pending = null;
    if(!lastCard) return;
    var r = lastCard.getBoundingClientRect();
    lastCard.style.setProperty('--mx', (lastX - r.left).toFixed(1) + 'px');
    lastCard.style.setProperty('--my', (lastY - r.top).toFixed(1) + 'px');
  }

  document.addEventListener('pointermove', function(e){
    if(e.pointerType === 'touch') return;
    var card = e.target && e.target.closest ? e.target.closest('.card.hoverable') : null;
    if(!card) return;
    lastCard = card; lastX = e.clientX; lastY = e.clientY;
    if(pending === null) pending = requestAnimationFrame(paint);
  }, { passive: true });
})();
