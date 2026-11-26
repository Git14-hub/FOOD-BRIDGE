document.addEventListener("DOMContentLoaded", function () {
    // Dependent District -> Area dropdown loader
    document.querySelectorAll("select[data-area-target]").forEach(function (districtSelect) {
        districtSelect.addEventListener("change", function () {
            var targetId = districtSelect.getAttribute("data-area-target");
            var areaSelect = document.getElementById(targetId);
            if (!areaSelect) return;

            var districtId = districtSelect.value;
            var includeAll = areaSelect.getAttribute("data-include-all") === "1";
            areaSelect.innerHTML = "";

            var placeholder = document.createElement("option");
            placeholder.value = "";
            placeholder.textContent = includeAll ? "All areas" : "Select area";
            areaSelect.appendChild(placeholder);

            if (!districtId) return;

            fetch("/api/areas/" + encodeURIComponent(districtId))
                .then(function (resp) { return resp.json(); })
                .then(function (data) {
                    (data.areas || []).forEach(function (area) {
                        var opt = document.createElement("option");
                        opt.value = area.id;
                        opt.textContent = area.name;
                        areaSelect.appendChild(opt);
                    });
                })
                .catch(function () {
                    // Server-side validation still protects submission if fetch fails.
                });
        });
    });

    // Registration role field toggle for NGO-specific fields
    var roleRadios = document.querySelectorAll("input[name='role'][data-ngo-toggle='1']");
    var ngoFields = document.getElementById("ngo-only-fields");
    if (roleRadios.length && ngoFields) {
        function updateNgoFields() {
            var selected = document.querySelector("input[name='role']:checked");
            ngoFields.style.display = selected && selected.value === "ngo" ? "block" : "none";
        }
        roleRadios.forEach(function (r) {
            r.addEventListener("change", updateNgoFields);
        });
        updateNgoFields();
    }
});
