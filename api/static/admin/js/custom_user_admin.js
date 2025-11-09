document.addEventListener("DOMContentLoaded", function () {
  const roleField = document.getElementById("id_role");

  // Map field names to their DOM elements
  const fields = {
    branch: document.querySelector(".form-row.field-branch"),
    year: document.querySelector(".form-row.field-year"),
    lab_day: document.querySelector(".form-row.field-lab_day"),
    lab_days: document.querySelector(".form-row.field-lab_days"),
    department: document.querySelector(".form-row.field-department"),
  };

  function toggleFields() {
    const role = roleField.value;

    // Hide all fields by default
    Object.values(fields).forEach((field) => {
      if (field) field.style.display = "none";
    });

    if (role === "student") {
      if (fields.branch) fields.branch.style.display = "";
      if (fields.year) fields.year.style.display = "";
      if (fields.lab_day) fields.lab_day.style.display = "";
    } else if (role === "faculty" || role === "staff") {
      if (fields.department) fields.department.style.display = "";
      if (fields.lab_days) fields.lab_days.style.display = "";
    }
    // admin sees nothing extra
  }

  if (roleField) {
    toggleFields(); // initial load
    roleField.addEventListener("change", toggleFields); // toggle on role change
  }
});
