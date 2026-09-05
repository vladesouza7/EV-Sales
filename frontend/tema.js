function alternarTema() {
  var raiz = document.documentElement;
  var novo = raiz.getAttribute("data-theme") === "light" ? "dark" : "light";
  raiz.setAttribute("data-theme", novo);
  localStorage.setItem("solvolt-tema", novo);
}

(function carregarTema() {
  var salvo = localStorage.getItem("solvolt-tema");
  if (salvo) {
    document.documentElement.setAttribute("data-theme", salvo);
  } else if (window.matchMedia("(prefers-color-scheme: light)").matches) {
    document.documentElement.setAttribute("data-theme", "light");
  }
})();
