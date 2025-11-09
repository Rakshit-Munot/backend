document.addEventListener("DOMContentLoaded", function () {
  const roleField = document.getElementById("id_role");
  const branchField = document.querySelector(".form-row.field-branch");
  const yearField = document.querySelector(".form-row.field-year");
  const departmentField = document.querySelector(".form-row.field-department");
  const labDaysField = document.querySelector(".form-row.field-lab_days");

  function toggleFields() {
    const role = roleField.value;

    // Hide all by default
    [branchField, yearField, departmentField, labDaysField].forEach(
      (field) => field && (field.style.display = "none")
    );

    if (role === "student") {
      if (branchField) branchField.style.display = "";
      if (yearField) yearField.style.display = "";
      if (labDaysField) labDaysField.style.display = "";
    } else if (role === "faculty" || role === "staff") {
      if (departmentField) departmentField.style.display = "";
      if (labDaysField) labDaysField.style.display = "";
    }
  }

  if (roleField) {
    toggleFields(); // initial load
    roleField.addEventListener("change", toggleFields); // on change
  }
});
