document.querySelectorAll("[data-tab-root]").forEach((root) => {
  const buttons = [...root.querySelectorAll("[data-tab-button]")];
  const panels = [...root.querySelectorAll("[data-tab-panel]")];
  const activate = (name) => {
    buttons.forEach((button) => button.classList.toggle("active", button.dataset.tabButton === name));
    panels.forEach((panel) => { panel.hidden = panel.dataset.tabPanel !== name; });
  };
  buttons.forEach((button) => button.addEventListener("click", () => activate(button.dataset.tabButton)));
  root.querySelectorAll("[data-open-tab]").forEach((button) => button.addEventListener("click", () => activate(button.dataset.openTab)));
  activate(root.dataset.defaultTab || "overview");
});
document.querySelectorAll("form[data-confirm]").forEach((form) => form.addEventListener("submit", (event) => {
  if (!window.confirm(form.dataset.confirm)) event.preventDefault();
}));

document.querySelectorAll("[data-image-dropzone]").forEach((dropzone) => {
  const input = dropzone.querySelector("[data-image-input]");
  const preview = dropzone.querySelector("[data-image-preview]");
  const placeholder = dropzone.querySelector("[data-upload-placeholder]");
  const change = dropzone.querySelector("[data-upload-change]");
  const filename = dropzone.parentElement.querySelector("[data-selected-file]");
  let previewUrl = null;

  const showFile = (file) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      input.setCustomValidity("Choose a JPG, PNG, or WebP image.");
      input.reportValidity();
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      input.setCustomValidity("The product image must be 8 MB or smaller.");
      input.reportValidity();
      return;
    }
    input.setCustomValidity("");
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    preview.src = previewUrl;
    preview.hidden = false;
    placeholder.hidden = true;
    change.hidden = false;
    filename.textContent = file.name;
  };

  input.addEventListener("change", () => showFile(input.files[0]));
  ["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragging");
  }));
  dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer.files[0];
    if (!file) return;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    showFile(file);
  });
});

document.querySelectorAll("[data-gcash-settings-form]").forEach((form) => {
  const accountInput = form.querySelector("[data-gcash-account]");
  const numberInput = form.querySelector("[data-gcash-number]");
  const providerInput = form.querySelector("[data-wallet-provider]");
  const accountPreview = document.querySelector("[data-live-account]");
  const numberPreview = document.querySelector("[data-live-number]");
  const providerPreview = document.querySelector("[data-live-provider]");
  const providerMark = document.querySelector("[data-live-provider-mark]");
  const dropzone = form.querySelector("[data-qr-dropzone]");
  const input = form.querySelector("[data-qr-input]");
  const preview = form.querySelector("[data-qr-preview]");
  const placeholder = form.querySelector("[data-qr-placeholder]");
  const change = form.querySelector("[data-qr-change]");
  const filename = form.querySelector("[data-qr-filename]");
  const livePreview = document.querySelector("[data-live-qr]");
  const livePlaceholder = document.querySelector("[data-live-qr-placeholder]");
  let previewUrl = null;

  const syncAccount = () => {
    accountPreview.textContent = accountInput.value.trim() || "Account name";
  };
  const syncNumber = () => {
    numberPreview.textContent = numberInput.value.trim() || "Account number";
  };
  const syncProvider = () => {
    if (!providerInput) return;
    const provider = providerInput.value.trim() || "E-wallet";
    providerPreview.textContent = provider;
    providerMark.textContent = provider.charAt(0).toUpperCase();
  };
  accountInput.addEventListener("input", syncAccount);
  numberInput.addEventListener("input", syncNumber);
  if (providerInput) providerInput.addEventListener("input", syncProvider);

  const showQr = (file) => {
    if (!file) return;
    const validTypes = ["image/jpeg", "image/png", "image/webp"];
    if (!validTypes.includes(file.type)) {
      input.setCustomValidity("Choose a JPG, PNG, or WebP QR image.");
      input.reportValidity();
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      input.setCustomValidity("The QR image must be 8 MB or smaller.");
      input.reportValidity();
      return;
    }
    input.setCustomValidity("");
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    preview.src = previewUrl;
    preview.hidden = false;
    placeholder.hidden = true;
    change.hidden = false;
    filename.textContent = file.name;
    livePreview.src = previewUrl;
    livePreview.hidden = false;
    if (livePlaceholder) livePlaceholder.hidden = true;
  };

  input.addEventListener("change", () => showQr(input.files[0]));
  ["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragging");
  }));
  dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer.files[0];
    if (!file) return;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    showQr(file);
  });
});

document.querySelectorAll("[data-receipt-form]").forEach((form) => {
  const dropzone = form.querySelector("[data-receipt-dropzone]");
  const input = form.querySelector("[data-receipt-input]");
  const preview = form.querySelector("[data-receipt-preview]");
  const placeholder = form.querySelector("[data-receipt-placeholder]");
  const change = form.querySelector("[data-receipt-change]");
  const filename = form.querySelector("[data-receipt-filename]");
  const submit = form.querySelector(".receipt-submit");
  let previewUrl = null;

  const showReceipt = (file) => {
    if (!file) return;
    const validTypes = ["image/jpeg", "image/png", "image/webp"];
    if (!validTypes.includes(file.type)) {
      input.setCustomValidity("Choose a JPG, PNG, or WebP receipt image.");
      input.reportValidity();
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      input.setCustomValidity("The receipt image must be 8 MB or smaller.");
      input.reportValidity();
      return;
    }
    input.setCustomValidity("");
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    preview.src = previewUrl;
    preview.hidden = false;
    placeholder.hidden = true;
    change.hidden = false;
    filename.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
    filename.classList.add("ready");
  };

  input.addEventListener("change", () => showReceipt(input.files[0]));
  ["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragging");
  }));
  dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer.files[0];
    if (!file) return;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    showReceipt(file);
  });
  form.addEventListener("submit", () => {
    if (!form.checkValidity()) return;
    form.classList.add("submitting");
    submit.textContent = "Submitting receipt…";
  });
});

document.querySelectorAll("[data-donation-form]").forEach((form) => {
  const tier = form.querySelector("[data-donation-tier]");
  const customField = form.querySelector("[data-custom-donation]");
  const customInput = form.querySelector("[data-custom-donation-input]");
  const syncCustomAmount = () => {
    const custom = tier.value === "custom";
    customField.hidden = !custom;
    customInput.disabled = !custom;
    customInput.required = custom;
    if (!custom) customInput.value = "";
  };
  tier.addEventListener("change", syncCustomAmount);
  syncCustomAmount();
});

const copyText = async (value) => {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const helper = document.createElement("textarea");
  helper.value = value;
  helper.setAttribute("readonly", "");
  helper.style.position = "fixed";
  helper.style.opacity = "0";
  document.body.appendChild(helper);
  helper.select();
  document.execCommand("copy");
  helper.remove();
};

const showActionFeedback = (element, message) => {
  const original = element.textContent;
  element.textContent = message;
  element.classList.add("action-done");
  window.setTimeout(() => {
    element.textContent = original;
    element.classList.remove("action-done");
  }, 1600);
};

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await copyText(button.dataset.copy);
      showActionFeedback(button, "Copied ✓");
    } catch (_error) {
      showActionFeedback(button, "Copy failed");
    }
  });
});

document.querySelectorAll("[data-share-url]").forEach((button) => {
  button.addEventListener("click", async () => {
    const data = {title: button.dataset.shareTitle || document.title, url: button.dataset.shareUrl};
    try {
      if (navigator.share) {
        await navigator.share(data);
      } else {
        await copyText(data.url);
        showActionFeedback(button, "Link copied ✓");
      }
    } catch (error) {
      if (error.name !== "AbortError") showActionFeedback(button, "Share failed");
    }
  });
});
