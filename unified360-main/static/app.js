document.addEventListener("DOMContentLoaded", () => {
  lucide.createIcons();
  const sidebar = document.getElementById("sidebar");
  const toggleBtn = document.getElementById("sidebarToggle");
  const collapsibles = document.querySelectorAll(".collapsible");

  // === Restore Saved Collapse State ===
  if (localStorage.getItem("sidebarCollapsed") === "true") {
    sidebar.classList.add("manual-collapsed");
  }

  // === Manual Collapsing via Toggle Button ===
  toggleBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    sidebar.classList.toggle("manual-collapsed");
    localStorage.setItem("sidebarCollapsed", sidebar.classList.contains("manual-collapsed"));
  });

  // === Expand/Collapse Submenus ===
  collapsibles.forEach((title) => {
    const targetId = title.getAttribute("data-target");
    const submenu = document.getElementById(targetId);

    title.addEventListener("click", (e) => {
      e.stopPropagation(); // Prevent accidental collapsing of sidebar

      submenu.classList.toggle("open");
      title.classList.toggle("open");
    });
  });

  // === Prevent collapse when clicking submenu links ===
  document.querySelectorAll(".submenu a").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.stopPropagation();
    });
  });
});

      
