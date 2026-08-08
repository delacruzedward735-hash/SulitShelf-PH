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

document.querySelectorAll("[data-crm-user-search]").forEach((input) => {
  const list = document.querySelector("[data-crm-user-list]");
  if (!list) return;
  const users = [...list.querySelectorAll("[data-crm-user]")];
  const empty = list.querySelector("[data-crm-search-empty]");
  input.addEventListener("input", () => {
    const query = input.value.trim().toLowerCase();
    let visible = 0;
    users.forEach((user) => {
      user.hidden = Boolean(query) && !user.dataset.crmUser.includes(query);
      if (!user.hidden) visible += 1;
    });
    if (empty) empty.hidden = visible !== 0;
  });
});

document.querySelectorAll("[data-crm-composer]").forEach((form) => {
  const recipient = form.querySelector("[data-crm-recipient]");
  const emailToggle = form.querySelector("[data-crm-email-toggle]");
  const emailOption = form.querySelector("[data-crm-email-option]");
  const emailHelp = form.querySelector("[data-crm-email-help]");
  const body = form.querySelector("[data-crm-body]");
  const count = form.querySelector("[data-crm-character-count]");

  const updateRecipientMode = () => {
    const broadcast = recipient?.value === "all";
    if (emailToggle) {
      emailToggle.disabled = broadcast;
      if (broadcast) emailToggle.checked = false;
    }
    emailOption?.classList.toggle("broadcast-disabled", broadcast);
    if (emailHelp) {
      emailHelp.textContent = broadcast
        ? "Broadcasts are delivered in-app to avoid email-provider and hosting timeouts."
        : "Available for one recipient through Resend or MailerSend.";
    }
  };
  const updateCount = () => {
    if (body && count) count.textContent = `${body.value.length.toLocaleString()} / 4,000`;
  };
  recipient?.addEventListener("change", updateRecipientMode);
  body?.addEventListener("input", updateCount);
  updateRecipientMode();
  updateCount();
});

document.querySelectorAll("[data-image-dropzone]").forEach((dropzone) => {
  const input = dropzone.querySelector("[data-image-input]");
  const preview = dropzone.querySelector("[data-image-preview]");
  const placeholder = dropzone.querySelector("[data-upload-placeholder]");
  const change = dropzone.querySelector("[data-upload-change]");
  const filename = dropzone.parentElement.querySelector("[data-selected-file]");
  const maxBytes = Number(dropzone.dataset.maxBytes) || (8 * 1024 * 1024);
  let previewUrl = null;

  const showFile = (file) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      input.setCustomValidity("Choose a JPG, PNG, or WebP image.");
      input.reportValidity();
      return;
    }
    if (file.size > maxBytes) {
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

document.querySelectorAll("[data-product-create-form]").forEach((form) => {
  const submit = form.querySelector("[data-product-submit]");
  const submitLabel = form.querySelector("[data-product-submit-label]");
  const status = form.querySelector("[data-product-form-status]");
  const draftKey = form.dataset.productDraftKey;
  const fields = [...form.elements].filter((field) => (
    field.name
    && field.name !== "csrf_token"
    && field.type !== "file"
    && field.type !== "submit"
  ));

  const showStatus = (message, tone = "info") => {
    if (!status) return;
    status.textContent = message;
    status.classList.remove("error", "info");
    status.classList.add(tone);
    status.hidden = false;
  };

  const readDraft = () => {
    if (!draftKey) return null;
    try {
      return JSON.parse(window.sessionStorage.getItem(draftKey) || "null");
    } catch (_error) {
      return null;
    }
  };

  const saveDraft = () => {
    if (!draftKey) return;
    const draft = {};
    fields.forEach((field) => {
      draft[field.name] = field.type === "checkbox" ? field.checked : field.value;
    });
    try {
      window.sessionStorage.setItem(draftKey, JSON.stringify(draft));
    } catch (_error) {
      // Some privacy modes disable session storage. Submission still works normally.
    }
  };

  const clearDraft = () => {
    if (!draftKey) return;
    try {
      window.sessionStorage.removeItem(draftKey);
    } catch (_error) {
      // Ignore unavailable storage; there is no persistent product data to clear.
    }
  };

  const params = new URLSearchParams(window.location.search);
  if (params.has("product_created")) {
    clearDraft();
  } else {
    const draft = readDraft();
    let restored = false;
    if (draft && typeof draft === "object") {
      fields.forEach((field) => {
        if (!Object.prototype.hasOwnProperty.call(draft, field.name)) return;
        if (field.type === "checkbox") {
          field.checked = Boolean(draft[field.name]);
          restored ||= field.checked;
          return;
        }
        if (!field.value && typeof draft[field.name] === "string") {
          field.value = draft[field.name];
          restored ||= Boolean(field.value);
        }
      });
    }
    if (restored) {
      showStatus("Your product details were restored. For security, please choose the product image again.");
    }
  }

  fields.forEach((field) => {
    field.addEventListener(field.type === "checkbox" || field.tagName === "SELECT" ? "change" : "input", saveDraft);
  });

  submit?.addEventListener("click", (event) => {
    if (form.checkValidity()) return;
    event.preventDefault();
    showStatus("Complete the highlighted required fields before publishing your product.", "error");
    const invalid = form.querySelector(":invalid");
    invalid?.closest("label")?.scrollIntoView({ behavior: "smooth", block: "center" });
    form.reportValidity();
  });

  form.addEventListener("submit", (event) => {
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    saveDraft();
    form.dataset.submitting = "true";
    form.classList.add("submitting");
    if (submit) submit.disabled = true;
    if (submitLabel) submitLabel.textContent = "Optimizing image & publishing…";
    showStatus("Uploading and optimizing your image. Keep this page open until publishing finishes.");
  });

  window.addEventListener("pageshow", () => {
    form.dataset.submitting = "false";
    form.classList.remove("submitting");
    if (submit) submit.disabled = false;
    if (submitLabel) submitLabel.textContent = "Publish product";
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

const storageList = (key) => {
  try {
    const value = JSON.parse(window.localStorage.getItem(key) || "[]");
    return Array.isArray(value) ? value.map(Number).filter(Number.isInteger).filter((item) => item > 0).slice(0, 30) : [];
  } catch (_error) {
    return [];
  }
};

const saveStorageList = (key, values) => {
  try {
    window.localStorage.setItem(key, JSON.stringify([...new Set(values)].slice(0, 30)));
  } catch (_error) {
    // Private browsing or a storage policy may disable local storage.
  }
};

const updateSavedUI = () => {
  const saved = storageList("sulitshelf_saved_products");
  document.querySelectorAll("[data-saved-count]").forEach((item) => { item.textContent = String(saved.length); });
  document.querySelectorAll("[data-save-product]").forEach((button) => {
    const selected = saved.includes(Number(button.dataset.saveProduct));
    button.classList.toggle("saved", selected);
    button.textContent = selected ? "♥ Saved" : "♡ Save";
    button.setAttribute("aria-pressed", String(selected));
  });
};

document.querySelectorAll("[data-save-product]").forEach((button) => {
  button.addEventListener("click", () => {
    const productId = Number(button.dataset.saveProduct);
    const saved = storageList("sulitshelf_saved_products");
    saveStorageList(
      "sulitshelf_saved_products",
      saved.includes(productId) ? saved.filter((item) => item !== productId) : [productId, ...saved],
    );
    updateSavedUI();
  });
});

document.querySelectorAll("[data-recent-product]").forEach((element) => {
  const productId = Number(element.dataset.recentProduct);
  const recent = storageList("sulitshelf_recent_products").filter((item) => item !== productId);
  saveStorageList("sulitshelf_recent_products", [productId, ...recent].slice(0, 12));
});
updateSavedUI();

const createSavedCard = (product, removable = false) => {
  const article = document.createElement("article");
  article.className = "saved-mini-card";
  const image = document.createElement("img");
  image.src = product.image_url;
  image.alt = product.name;
  image.loading = "lazy";
  const content = document.createElement("div");
  const label = document.createElement("small");
  label.textContent = `${product.marketplace} · ${product.department}`;
  const heading = document.createElement("h3");
  const link = document.createElement("a");
  link.href = product.detail_url;
  link.textContent = product.name;
  heading.appendChild(link);
  const shop = document.createElement("span");
  shop.textContent = product.shop_name;
  const price = document.createElement("strong");
  price.textContent = product.price;
  const actions = document.createElement("div");
  const view = document.createElement("a");
  view.href = product.detail_url;
  view.className = "button primary";
  view.textContent = "View product";
  actions.appendChild(view);
  if (removable) {
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      const saved = storageList("sulitshelf_saved_products").filter((item) => item !== product.id);
      saveStorageList("sulitshelf_saved_products", saved);
      article.remove();
      updateSavedUI();
    });
    actions.appendChild(remove);
  }
  content.append(label, heading, shop, price, actions);
  article.append(image, content);
  return article;
};

const savedPage = document.querySelector("[data-saved-page]");
if (savedPage) {
  const loadList = async (key, targetSelector, removable) => {
    const ids = storageList(key);
    const target = document.querySelector(targetSelector);
    if (!target || !ids.length) return;
    try {
      const response = await fetch(`${savedPage.dataset.productsApi}?ids=${ids.join(",")}&source=saved`, {headers: {Accept: "application/json"}});
      if (!response.ok) return;
      const payload = await response.json();
      target.replaceChildren(...payload.products.map((product) => createSavedCard(product, removable)));
    } catch (_error) {
      // Keep the helpful empty/fallback state when offline.
    }
  };
  loadList("sulitshelf_saved_products", "[data-saved-products]", true);
  loadList("sulitshelf_recent_products", "[data-recent-products]", false);
  document.querySelector("[data-clear-saved]")?.addEventListener("click", () => {
    saveStorageList("sulitshelf_saved_products", []);
    window.location.reload();
  });
}

const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
const impressionGroups = new Map();
document.querySelectorAll("[data-product-impression]").forEach((element) => {
  const source = element.dataset.impressionSource || "mall";
  if (!impressionGroups.has(source)) impressionGroups.set(source, new Set());
  impressionGroups.get(source).add(Number(element.dataset.productImpression));
});
impressionGroups.forEach((ids, source) => {
  fetch("/events/impressions", {
    method: "POST",
    credentials: "same-origin",
    headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken, Accept: "application/json"},
    body: JSON.stringify({product_ids: [...ids], source}),
    keepalive: true,
  }).catch(() => {});
});

document.querySelectorAll("[data-reload-page]").forEach((button) => button.addEventListener("click", () => window.location.reload()));

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/service-worker.js").catch(() => {}));
}

document.querySelectorAll("[data-password-toggle]").forEach((button) => {
  const input = button.closest(".auth-password-wrap")?.querySelector("input");
  if (!input) return;
  button.addEventListener("click", () => {
    const showing = input.type === "text";
    input.type = showing ? "password" : "text";
    button.textContent = showing ? "Show" : "Hide";
    button.setAttribute("aria-label", showing ? "Show password" : "Hide password");
    button.setAttribute("aria-pressed", String(!showing));
    input.focus({preventScroll: true});
  });
});

document.querySelectorAll("[data-password-strength-input]").forEach((input) => {
  const meter = input.closest("form")?.querySelector("[data-password-strength]");
  if (!meter) return;
  const updateStrength = () => {
    const value = input.value;
    let score = 0;
    if (value.length >= 12) score += 1;
    if (value.length >= 16) score += 1;
    if (/[a-z]/.test(value) && /[A-Z]/.test(value)) score += 1;
    if (/\d/.test(value) && /[^A-Za-z0-9]/.test(value)) score += 1;
    const level = Math.min(score, 4);
    meter.dataset.level = String(level);
    const labels = ["Use 12–128 characters", "More length will make this stronger", "Good start—add more variety", "Strong password", "Very strong password"];
    meter.querySelector("span").textContent = labels[level];
  };
  input.addEventListener("input", updateStrength);
  updateStrength();
});

document.querySelectorAll("[data-auth-form]").forEach((form) => {
  form.addEventListener("submit", () => {
    if (!form.checkValidity()) return;
    form.classList.add("submitting");
    const label = form.querySelector(".auth-submit > span");
    if (label) label.textContent = form.dataset.submitLabel || "Please wait…";
  });
});

const formatUploadSize = (bytes) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

document.querySelectorAll("[data-csv-picker]").forEach((picker) => {
  const input = picker.querySelector("[data-csv-input]");
  const title = picker.querySelector("[data-csv-title]");
  const filename = picker.querySelector("[data-csv-filename]");
  const action = picker.querySelector("em");
  const maxBytes = Number(picker.dataset.maxBytes || 2000000);
  const initialTitle = title?.textContent || "Choose a CSV file";
  const initialFilename = filename?.textContent || "Maximum 2 MB";
  if (!input) return;

  const showFile = (file, reportError = false) => {
    picker.classList.remove("ready", "invalid");
    input.setCustomValidity("");
    if (!file) {
      if (title) title.textContent = initialTitle;
      if (filename) filename.textContent = initialFilename;
      if (action) action.textContent = "Choose CSV";
      return false;
    }
    let error = "";
    if (!file.name.toLowerCase().endsWith(".csv")) error = "Choose a file that ends in .csv.";
    else if (file.size > maxBytes) error = `This CSV is larger than ${formatUploadSize(maxBytes)}.`;
    else if (!file.size) error = "This CSV file is empty.";
    if (error) {
      input.value = "";
      input.setCustomValidity(error);
      picker.classList.add("invalid");
      if (title) title.textContent = "That file cannot be imported";
      if (filename) filename.textContent = error;
      if (action) action.textContent = "Choose again";
      if (reportError) input.reportValidity();
      return false;
    }
    picker.classList.add("ready");
    if (title) title.textContent = file.name;
    if (filename) filename.textContent = `${formatUploadSize(file.size)} · ready to validate`;
    if (action) action.textContent = "Change CSV";
    return true;
  };

  input.addEventListener("change", () => showFile(input.files?.[0], true));
  ["dragenter", "dragover"].forEach((type) => picker.addEventListener(type, (event) => {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    picker.classList.add("dragging");
  }));
  ["dragleave", "dragend"].forEach((type) => picker.addEventListener(type, () => picker.classList.remove("dragging")));
  picker.addEventListener("drop", (event) => {
    event.preventDefault();
    picker.classList.remove("dragging");
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    try {
      input.files = event.dataTransfer.files;
    } catch (_error) {
      input.setCustomValidity("Use the Choose CSV button to select this file in your browser.");
      input.reportValidity();
      return;
    }
    showFile(file, true);
  });
});

document.querySelectorAll("[data-import-form]").forEach((form) => {
  form.addEventListener("submit", () => {
    if (!form.checkValidity()) return;
    form.classList.add("submitting");
    const label = form.querySelector("[data-import-label]");
    if (label) label.textContent = "Checking your CSV…";
  });
});

document.querySelectorAll("[data-branding-form]").forEach((form) => {
  const preview = form.querySelector("[data-branding-live]");
  const nameInput = form.querySelector("[data-branding-name]");
  const taglineInput = form.querySelector("[data-branding-tagline]");
  const liveName = form.querySelector("[data-branding-live-name]");
  const liveTagline = form.querySelector("[data-branding-live-tagline]");
  const liveLogoPlaceholder = form.querySelector("[data-branding-live-logo-placeholder]");
  const poweredLabel = form.querySelector("[data-branding-powered]");
  const hidePowered = form.querySelector("[data-hide-platform-branding]");
  const themeClasses = ["orange", "purple", "blue", "green", "midnight"].map((theme) => `shop-theme-${theme}`);

  const syncCopy = () => {
    const shopName = nameInput?.value.trim() || "Your shop";
    if (liveName) liveName.textContent = shopName;
    if (liveTagline) liveTagline.textContent = taglineInput?.value.trim() || "Curated finds worth checking out.";
    if (liveLogoPlaceholder) liveLogoPlaceholder.textContent = shopName.slice(0, 1).toUpperCase();
  };
  nameInput?.addEventListener("input", syncCopy);
  taglineInput?.addEventListener("input", syncCopy);
  syncCopy();

  form.querySelectorAll("[data-branding-theme]").forEach((radio) => radio.addEventListener("change", () => {
    if (!preview || !radio.checked) return;
    preview.classList.remove(...themeClasses);
    preview.classList.add(`shop-theme-${radio.value}`);
  }));
  hidePowered?.addEventListener("change", () => {
    if (poweredLabel) poweredLabel.hidden = hidePowered.checked;
  });

  form.querySelectorAll("[data-branding-upload]").forEach((uploader) => {
    const input = uploader.querySelector("[data-branding-input]");
    const localPreview = uploader.querySelector("[data-branding-local-preview]");
    const localPlaceholder = uploader.querySelector("[data-branding-placeholder]");
    const kind = uploader.dataset.brandingKind;
    const liveImage = form.querySelector(`[data-branding-live-${kind}]`);
    const livePlaceholder = kind === "logo" ? liveLogoPlaceholder : null;
    const fileLabel = form.querySelector(`[data-branding-file-name="${kind}"]`);
    const remove = form.querySelector(`[data-branding-remove="${kind}"]`);
    const initialLocalSource = localPreview?.getAttribute("src") || "";
    const initialLiveSource = liveImage?.getAttribute("src") || "";
    let objectUrl = "";
    if (!input || !localPreview) return;

    const setSource = (image, source) => {
      if (!image) return;
      if (source) {
        image.src = source;
        image.hidden = false;
      } else {
        image.removeAttribute("src");
        image.hidden = true;
      }
    };
    const showFile = (file, reportError = false) => {
      uploader.classList.remove("ready", "invalid");
      input.setCustomValidity("");
      if (!file) return false;
      const allowedTypes = ["image/jpeg", "image/png", "image/webp"];
      let error = "";
      if (!allowedTypes.includes(file.type)) error = "Choose a JPG, PNG, or WebP image.";
      else if (file.size > 8 * 1024 * 1024) error = "This image is larger than 8 MB.";
      else if (!file.size) error = "This image file is empty.";
      if (error) {
        input.value = "";
        input.setCustomValidity(error);
        uploader.classList.add("invalid");
        if (fileLabel) fileLabel.textContent = error;
        if (reportError) input.reportValidity();
        return false;
      }
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(file);
      setSource(localPreview, objectUrl);
      setSource(liveImage, objectUrl);
      if (localPlaceholder) localPlaceholder.hidden = true;
      if (livePlaceholder) livePlaceholder.hidden = true;
      if (remove) remove.checked = false;
      uploader.classList.add("ready");
      if (fileLabel) fileLabel.textContent = `${file.name} · ${formatUploadSize(file.size)} · ready to save`;
      return true;
    };

    input.addEventListener("change", () => showFile(input.files?.[0], true));
    ["dragenter", "dragover"].forEach((type) => uploader.addEventListener(type, (event) => {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      uploader.classList.add("dragging");
    }));
    ["dragleave", "dragend"].forEach((type) => uploader.addEventListener(type, () => uploader.classList.remove("dragging")));
    uploader.addEventListener("drop", (event) => {
      event.preventDefault();
      uploader.classList.remove("dragging");
      const file = event.dataTransfer?.files?.[0];
      if (!file) return;
      try {
        input.files = event.dataTransfer.files;
      } catch (_error) {
        input.setCustomValidity("Use the Choose image button to select this file in your browser.");
        input.reportValidity();
        return;
      }
      showFile(file, true);
    });

    remove?.addEventListener("change", () => {
      if (remove.checked) {
        input.value = "";
        uploader.classList.remove("ready", "invalid");
        setSource(localPreview, "");
        setSource(liveImage, "");
        if (localPlaceholder) localPlaceholder.hidden = false;
        if (livePlaceholder) livePlaceholder.hidden = false;
        if (fileLabel) fileLabel.textContent = `Current ${kind} will be removed when you save`;
      } else {
        setSource(localPreview, initialLocalSource);
        setSource(liveImage, initialLiveSource);
        if (localPlaceholder) localPlaceholder.hidden = Boolean(initialLocalSource);
        if (livePlaceholder) livePlaceholder.hidden = Boolean(initialLiveSource);
        if (fileLabel) fileLabel.textContent = initialLocalSource ? `Current ${kind} is active` : `No ${kind} selected`;
      }
    });
  });

  form.addEventListener("submit", () => {
    if (!form.checkValidity()) return;
    form.classList.add("submitting");
    const label = form.querySelector(".settings-save-row .button > span");
    if (label) label.textContent = "Saving storefront…";
  });
});

document.querySelectorAll("[data-two-factor-code]").forEach((input) => {
  input.addEventListener("input", () => {
    const value = input.value.toUpperCase().replace(/\s+/g, "");
    input.value = /^\d*$/.test(value) ? value.slice(0, 6) : value.replace(/[^A-Z0-9-]/g, "").slice(0, 24);
  });
});

document.querySelectorAll("[data-print-page]").forEach((button) => {
  button.addEventListener("click", () => window.print());
});
