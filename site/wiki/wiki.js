const search = document.querySelector('#wiki-search');
const articles = [...document.querySelectorAll('.wiki-content article')];
const empty = document.querySelector('#no-results');

search?.addEventListener('input', () => {
  const query = search.value.trim().toLowerCase();
  let visible = 0;
  articles.forEach((article) => {
    const haystack = `${article.dataset.search || ''} ${article.textContent}`.toLowerCase();
    const matches = !query || haystack.includes(query);
    article.hidden = !matches;
    if (matches) visible += 1;
  });
  empty.hidden = visible !== 0;
});
